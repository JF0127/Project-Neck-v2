#include <cmath>

#include "neck/controller.h"
#include "probe.hpp"
#include "neck_sweep.h"
#include "neck_stream.h"
#include "ethercat/periodic_clock.h"
#include "ethercat/queue.h"

#include <boost/property_tree/json_parser.hpp>
#include <arpa/inet.h>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <exception>
#include <functional>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <unistd.h>

std::atomic<bool> neck_bus_ready{false};

namespace {
using Clock = std::chrono::steady_clock;
using Triple = std::array<double, 3>;
using MitValues = std::array<double, 5>; // kp, kd, pos_rad, spd_rad_s, tor_Nm
constexpr double RAD_TO_DEG = 57.29577951308232;
double mono() { return PeriodicClock::nowNs() * 1e-9; }
struct RippleComp {
    double gain, lead_s;
    std::array<std::array<std::array<double, 500>, 2>, 3> tables{};
    RippleComp(double gain, const std::array<int, 3>& ids, double lead_ms) : gain(gain), lead_s(lead_ms / 1000) {
        if (!std::isfinite(gain)) throw std::runtime_error("Comp must be finite");
        if (!std::isfinite(lead_ms)) throw std::runtime_error("Lead must be finite");
        if (!gain) return;
        boost::property_tree::ptree config;
        boost::property_tree::read_json(
            (std::filesystem::path(NECK_CALIBRATION_PATH).parent_path() / "ripple_comp.json").string(), config);
        for (int i = 0; i < 3; ++i) for (int d = 0; d < 2; ++d) {
            const auto& values = config.get_child("motors." + std::to_string(ids[i]) +
                                                  (d ? ".negative" : ".positive"));
            if (values.size() != 500) throw std::runtime_error("Ripple table must contain 500 values");
            int j = 0;
            for (const auto& value : values) {
                const double torque = value.second.get_value<double>();
                if (!std::isfinite(torque)) throw std::runtime_error("Invalid ripple torque");
                tables[i][d][j++] = torque;
            }
        }
    }
    double torque(int motor, double angle, double velocity) const {
        if (!gain) return 0;
        double phase = std::fmod(angle + velocity * lead_s, 10.0);
        if (!std::isfinite(phase)) throw std::runtime_error("Invalid ripple lookup angle");
        if (phase < 0) phase += 10;
        const double x = phase * 50, fraction = x - std::floor(x);
        const int j = int(x) % 500;
        const auto& t = tables[motor];
        const double positive = t[0][j] * (1 - fraction) + t[0][(j + 1) % 500] * fraction;
        const double negative = t[1][j] * (1 - fraction) + t[1][(j + 1) % 500] * fraction;
        const double w = std::tanh(velocity); // deg/s，按 1°/s 归一化。
        return gain * (0.5 * (1 + w) * positive + 0.5 * (1 - w) * negative);
    }
};
struct Feedback {
    Triple value{}; // position_deg, velocity_deg_s, current_A
    std::array<Clock::time_point, 3> observed{};
    double temperature = std::numeric_limits<double>::quiet_NaN();
    int error = 0;
    int status = 0; // 反馈帧首字节（含 ACK 类型和错误码）
};
struct Sample { Triple rpy, q, velocity; };
struct CommandRow {
    double timestamp, elapsed, plan_time;
    Sample sample;
    std::array<Feedback, 3> feedback;
    Triple ages;
    EtherCAT_Msg frame;
    Triple compensation{};
    double buffer_ms = 0;
    int state = 0, step_id = 0;
    std::uint64_t dropped_samples = 0;
    double last_rx_mono_s = 0;
};
std::mutex feedback_mutex;
std::map<int, Feedback> feedback;
struct FeedbackRow {
    double timestamp;
    std::array<Triple, 3> measured;
    Triple commanded;
    std::array<MitValues, 3> mit_command{};
    Triple compensation{};
};
bool feedback_logging = false;
std::array<int, 3> feedback_ids;
std::vector<FeedbackRow> feedback_rows;

// 从官方报文反解实际下发值，包含 SDK 的限幅和量化。
MitValues commandMit(const Motor_Msg& motor) {
    const auto* d = motor.data;
    return {uint_to_float(((d[0] & 0x1f) << 7) | (d[1] >> 1), 0, 500, 12),
            uint_to_float(((d[1] & 1) << 8) | d[2], 0, 5, 9),
            uint_to_float((d[3] << 8) | d[4], -12.5f, 12.5f, 16),
            uint_to_float((d[5] << 4) | (d[6] >> 4), -18, 18, 12),
            uint_to_float(((d[6] & 0x0f) << 8) | d[7], -30, 30, 12)};
}
double sendMit(EtherCAT_Msg& frame, int channel, int id, float kp, float kd,
               double angle, double velocity, double compensation, double friction) {
    send_motor_ctrl_cmd(&frame, channel, id, kp, kd, angle / RAD_TO_DEG, velocity / RAD_TO_DEG,
                        friction + compensation);
    // 补偿列记录叠加前后协议力矩之差，包含限幅／量化；原 tor_Nm 列仍记录总力矩。
    const float base = std::clamp(float(friction), -30.0f, 30.0f);
    return commandMit(frame.motor[channel - 1])[4] -
           uint_to_float(float_to_uint(base, -30, 30, 12), -30, 30, 12);
}
void mitColumns(std::ostream& csv, const std::string& prefix) {
    for (const char* field : {"kp", "kd", "pos_rad", "spd_rad_s", "tor_Nm"})
        csv << prefix << field;
}
void mainColumns(std::ofstream& csv, const std::array<int, 3>& ids) {
    csv << "timestamp_unix_s,elapsed_s,plan_time_s,roll_deg,pitch_deg,yaw_deg";
    for (int id : ids) {
        const std::string prefix = ",motor" + std::to_string(id);
        csv << prefix << "_cmd_position_deg" << prefix << "_cmd_velocity_deg_s"
            << prefix << "_speed_limit_deg_s" << prefix << "_feedback_position_deg"
            << prefix << "_feedback_velocity_deg_s" << prefix << "_feedback_current_A"
            << prefix << "_feedback_pdo_age_ms" << prefix << "_speed_limit_raw_0p1rpm"
            << prefix << "_current_limit_raw_0p1A" << prefix << "_temperature_degC"
            << prefix << "_error_code" << prefix << "_status_raw";
        mitColumns(csv, prefix + "_cmd_");
        csv << prefix << "_cmd_comp_tor_Nm";
    }
    csv << ",target_roll_deg,target_pitch_deg,target_yaw_deg";
    csv << ",buffer_ms,state,dropped_samples,last_rx_mono_s,step_id";
    csv << '\n' << std::fixed << std::setprecision(9);
    csv.flush();
}

// 两种日志均由后台线程格式化、写盘；命令循环只向预分配的单生产者队列复制数值。
struct FeedbackLog {
    std::ofstream csv;
    std::ofstream& main_csv;
    spsc_queue<CommandRow> commands;
    std::vector<FeedbackRow> pending;
    std::atomic<bool> stopping{false};
    std::thread writer;
    FeedbackLog(const std::string& path, const std::array<int, 3>& ids,
                size_t count, std::ofstream& main_csv)
        : csv(path), main_csv(main_csv), commands(count + 2) {
        csv << "timestamp_unix_s";
        for (int id : ids) {
            const std::string prefix = ",motor" + std::to_string(id);
            csv << prefix << "_feedback_position_deg" << prefix << "_feedback_velocity_deg_s"
                << prefix << "_feedback_current_A" << prefix << "_active_cmd_position_deg";
            mitColumns(csv, prefix + "_active_cmd_");
            csv << prefix << "_active_cmd_comp_tor_Nm";
        }
        csv << '\n' << std::fixed << std::setprecision(9);
        std::lock_guard<std::mutex> lock(feedback_mutex);
        feedback_ids = ids;
        feedback_rows.clear();
        feedback_rows.reserve(256);
        pending.reserve(256);
        feedback_logging = true;
        writer = std::thread([this] {
            while (!stopping.load()) {
                writePending();
                std::this_thread::sleep_for(std::chrono::milliseconds(20));
            }
        });
    }
    void writePending() {
        commands.consume_all([this](const CommandRow& row) {
            auto& out = main_csv;
            const auto& sample = row.sample;
            out << row.timestamp << ',' << row.elapsed << ',' << row.plan_time;
            for (double angle : sample.rpy) out << ',' << angle;
            for (int i = 0; i < 3; ++i) {
                out << ',' << sample.q[i] << ',' << sample.velocity[i] << ",nan";
                for (double value : row.feedback[i].value) out << ',' << value;
                out << ',' << row.ages[i] << ",nan,nan";
                out << ',' << row.feedback[i].temperature << ',' << row.feedback[i].error << ',' << row.feedback[i].status;
                for (double value : commandMit(row.frame.motor[i])) out << ',' << value;
                out << ',' << row.compensation[i];
            }
            for (double angle : sample.rpy) out << ',' << angle;
            static const char* states[] = {"等待", "跟随", "外推减速", "保持", "过渡"};
            out << ',' << row.buffer_ms << ',' << states[row.state]
                << ',' << row.dropped_samples << ',' << row.last_rx_mono_s << ',' << row.step_id;
            out << '\n';
        });
        pending.clear();
        {
            std::lock_guard<std::mutex> lock(feedback_mutex);
            pending.swap(feedback_rows); // 复用两块缓冲，避免每 20 ms 重新分配／释放。
        }
        for (const auto& row : pending) {
            csv << row.timestamp;
            for (int i = 0; i < 3; ++i) {
                for (double value : row.measured[i]) csv << ',' << value;
                csv << ',' << row.commanded[i];
                for (double value : row.mit_command[i]) csv << ',' << value;
                csv << ',' << row.compensation[i];
            }
            csv << '\n';
        }
    }
    ~FeedbackLog() {
        {
            std::lock_guard<std::mutex> lock(feedback_mutex);
            feedback_logging = false;
        }
        stopping = true;
        writer.join();
        writePending();
    }
};
double magnitude(double value) { return value < 0 ? -value : value; }
void checkBus() {
    if (!running.load() || !neck_bus_ready.load())
        throw std::runtime_error("EtherCAT not ready: require OP, valid WKC and matching PDO sizes");
}
std::array<Feedback, 3> snapshot(const std::array<int, 3>& ids) {
    std::lock_guard<std::mutex> lock(feedback_mutex);
    const std::array<Feedback, 3> probe_values{feedback[ids[0]], feedback[ids[1]], feedback[ids[2]]};
    probe::send("motor_feedback_degrees", std::vector<double>{
        probe_values[0].value[0], probe_values[1].value[0], probe_values[2].value[0],
        probe_values[0].value[1], probe_values[1].value[1], probe_values[2].value[1],
        probe_values[0].value[2], probe_values[1].value[2], probe_values[2].value[2]});
    return probe_values;
}
struct Cancelled {};
Triple freshMotors(const std::array<int, 3>& ids) {
    const auto values = snapshot(ids);
    Triple q;
    for (int i = 0; i < 3; ++i) {
        q[i] = values[i].value[0];
        if (Clock::now() - values[i].observed[0] > std::chrono::milliseconds(100)) {
            EtherCAT_Msg request{};
            Motor_Msg reply{};
            get_motor_parameter(&request, i + 1, ids[i], param_get_pos);
            probe::send("drive_session_events", "refresh_position: " + std::to_string(ids[i]));
            if (!queryMotor(0, i + 1, request.motor[i], reply) || (reply.data[0] & 0x1f))
                throw std::runtime_error("No fresh position feedback for motor " + std::to_string(ids[i]));
            q[i] = motorParameterValue(reply);
        }
        if (!std::isfinite(q[i])) throw std::runtime_error("Invalid motor position feedback");
    }
    return q;
}
neck::Status poseStatus(const neck::Model& model, const Triple& q) {
    const auto pose = model.evaluate(q);
    neck::Status result{q, model.forward(q), pose.margin, pose.usable, {}};
    std::ostringstream out;
    for (int i = 0; i < 3; ++i)
        out << "motor" << model.ids[i] << '=' << q[i] << " deg [" << model.lower[i] << ','
            << model.upper[i] << "]" << (q[i] < model.lower[i] || q[i] > model.upper[i] ? " OUT" : "") << "; ";
    out << "transmission margin=" << pose.margin << " deg (required >= " << model.min_margin << ')';
    result.detail = out.str();
    return result;
}
std::array<Feedback, 3> queryFeedback(const std::array<int, 3>& ids, const std::atomic<bool>& cancel) {
    for (int field = 0; field < 3; ++field) {
        EtherCAT_Msg frame{};
        for (int i = 0; i < 3; ++i)
            get_motor_parameter(&frame, i + 1, ids[i], field + 1);
        auto requested = Clock::now();
        probe::send("drive_session_events", field == 0 ? "query_position" :
                    (field == 1 ? "query_velocity" : "query_current"));
        writeNeckDirect(frame); // 只查询位置、速度、电流，不发送运动目标。
        for (;;) {
            if (cancel.load()) throw Cancelled{};
            if (!running.load()) {
                EtherCAT_Init_Failure("query feedback: bus stopped");
                checkBus();
            }
            auto values = snapshot(ids);
            bool ready = neck_bus_ready.load() && !neckDirectPending();
            for (const auto& value : values)
                ready = ready && value.observed[field] >= requested;
            if (ready) break;
            if (Clock::now() - requested > std::chrono::seconds(1)) {
                static const char* steps[] = {"query position timeout; retry",
                                             "query velocity timeout; retry",
                                             "query current timeout; retry"};
                EtherCAT_Init_Failure(steps[field]);
                probe::send("drive_session_events", steps[field]);
                requested = Clock::now();
                writeNeckDirect(frame);
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
    }
    auto values = snapshot(ids);
    for (const auto& value : values) {
        if (value.error || !std::isfinite(value.value[0]) || !std::isfinite(value.value[1]) ||
            !std::isfinite(value.value[2]) || magnitude(value.value[1]) > 0.5) {
            EtherCAT_Init_Failure("validate initial motor feedback");
            throw std::runtime_error("Motor fault, invalid feedback or motor still moving; cannot start sweep");
        }
    }
    return values;
}
} // namespace

void recordNeckFeedback(const EtherCAT_Msg& raw, const OD_Motor_Msg decoded[6],
                        const EtherCAT_Msg& sent, const Triple& compensation) {
    std::lock_guard<std::mutex> lock(feedback_mutex);
    const auto now = Clock::now();
    for (int i = 0; i < 3; ++i) { // 固定使用从站 0 的 CAN1，按电机 ID 关联而非接收槽号。
        const auto& frame = raw.motor[i];
        const auto& value = decoded[i];
        if (!frame.dlc || frame.id == 0 || frame.id >= 0x7ff) continue;
        auto& out = feedback[frame.id];
        out.status = frame.data[0];
        const int ack = frame.data[0] >> 5;
        if (ack == 1 && frame.dlc >= 7) {
            out.value = {value.angle_actual_rad * RAD_TO_DEG,
                         value.speed_actual_rad * RAD_TO_DEG, value.current_actual_float};
            out.observed.fill(now);
            out.temperature = (int(frame.data[6]) - 50) / 2.0;
            out.error = value.error;
        } else if (ack == 2 && frame.dlc == 8) {
            out.value[0] = value.angle_actual_float;
            out.value[2] = value.current_actual_float;
            out.observed[0] = out.observed[2] = now;
            out.error = value.error;
        } else if (ack == 5 && frame.dlc == 6) {
            const int field = frame.data[1] - 1;
            if (field < 0 || field > 2) continue;
            const Triple measured = {value.angle_actual_float,
                                     value.speed_actual_float * 6.0, value.current_actual_float};
            out.value[field] = measured[field];
            out.observed[field] = now;
            out.error = value.error;
        }
    }
    // 跳过查询阶段；从首个混合模式 PDO 开始，每次有效刷新保存一行。
    if (feedback_logging && sent.can_ide == 1) {
        FeedbackRow row;
        row.compensation = compensation;
        row.timestamp = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        for (int i = 0; i < 3; ++i) {
            row.measured[i] = feedback[feedback_ids[i]].value;
            row.mit_command[i] = commandMit(sent.motor[i]);
            row.commanded[i] = row.mit_command[i][2] * RAD_TO_DEG;
        }
        feedback_rows.push_back(row);
    }
}

namespace neck {
const std::map<std::string, double>& defaults() {
    static const auto values = [] {
        boost::property_tree::ptree config;
        boost::property_tree::read_json(NECK_RUNTIME_PATH, config);
        std::map<std::string, double> result;
        for (const auto& entry : config) result[entry.first] = entry.second.get_value<double>();
        return result;
    }();
    return values;
}

struct Controller::Impl {
    std::map<std::string, double> options = defaults();
    Model model{NECK_CALIBRATION_PATH};
    std::unique_ptr<Stream> stream;
    std::unique_ptr<RippleComp> ripple;
    std::unique_ptr<PeriodicClock> cycle;
    std::ofstream csv;
    std::unique_ptr<FeedbackLog> log;
    std::shared_ptr<int> udp;
    double epoch = 0, previous = 0, hold_until = 0, clock_rate = 0;
    std::mutex mutex;
    std::condition_variable changed;
    std::deque<std::function<void()>> jobs;
    size_t pending = 0;
    bool closing = false, started = false, idle = true, armed = false, working = false;
    std::atomic<bool> cancel{false};
    std::map<double, int> step_ids;
    State state = State::Waiting;
    int step_id = 0; // +n：第 n 个时长的去程；-n：回程；0：非测试段。
    Vec measured_rpy{}, measured_q{}, target_rpy{};
    std::exception_ptr error;
    std::thread worker;

    Impl() : worker([this] { run(); }) {}
    ~Impl() {
        {
            std::lock_guard<std::mutex> lock(mutex);
            closing = true;
            cancel = true;
        }
        changed.notify_all();
        worker.join();
    }
    void post(std::function<void()> job) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            jobs.push_back(std::move(job));
            ++pending;
        }
        changed.notify_all();
    }
    Vec measured() { return freshMotors(model.ids); }
    Status requireUsable(bool allow_outside = false) {
        const auto current = poseStatus(model, measured());
        if (!current.usable && !allow_outside) throw std::runtime_error("Current pose out of range: " + current.detail);
        return current;
    }
    void move(const Vec& q, const Vec& target, double duration) {
        const bool target_usable = model.evaluate(q).usable;
        const auto from = requireUsable(target_usable);
        install(moveTrajectory(from.rpy, target, duration), mono(), 1, 0, target_usable);
    }
    void startSession() {
        probe::send("drive_session_events", "start");
        if (!running.load()) {
            EtherCAT_Init_Failure("start neck session: bus not running");
            throw std::runtime_error("EtherCAT initialization failed");
        }
        std::cout << "EtherCAT: connected.\n";
        const auto initial = queryFeedback(model.ids, cancel);
        Vec q;
        for (int i = 0; i < 3; ++i) q[i] = initial[i].value[0];
        clock_rate = options.at("rate");
        cycle = std::make_unique<PeriodicClock>(clock_rate);
        epoch = cycle->start * 1e-9;
        previous = epoch - 1 / clock_rate;
        stream = std::make_unique<Stream>(model, q, epoch);
        armed = model.evaluate(q).usable; // 超限时等待目标在可用范围内的点位命令。
        if (options.at("log")) {
            std::filesystem::create_directories(NECK_SWEEP_DATA_DIR);
            const auto stamp = std::chrono::system_clock::now().time_since_epoch();
            std::ostringstream name;
            name << NECK_SWEEP_DATA_DIR << "/session_rate" << clock_rate << '_'
                 << std::chrono::duration_cast<std::chrono::microseconds>(stamp).count() << ".csv";
            csv.open(name.str());
            mainColumns(csv, model.ids);
            const auto fb_name = name.str().substr(0, name.str().size() - 4) + "_fb.csv";
            log = std::make_unique<FeedbackLog>(fb_name, model.ids, size_t(clock_rate * 2), csv);
            std::cout << "CSV: " << name.str() << "\nFeedback CSV: " << fb_name << std::endl;
        }
        std::lock_guard<std::mutex> lock(mutex);
        measured_q = q;
        measured_rpy = target_rpy = model.forward(q);
        const auto status = poseStatus(model, q);
        std::cout << status.detail << "; RPY=" << measured_rpy[0] << ' ' << measured_rpy[1] << ' '
                  << measured_rpy[2] << "; usable=" << (status.usable ? "yes" : "no") << '\n';
    }
    void closeSession() {
        probe::send("drive_session_events", "stop");
        udp.reset();
        stream.reset();
        log.reset();
        if (csv.is_open()) csv.close();
        cycle.reset();
        armed = false;
        step_ids.clear();
        hold_until = 0;
        step_id = 0;
        finishNeckDirect();
        std::lock_guard<std::mutex> lock(mutex);
        state = State::Waiting;
        idle = true;
    }
    void install(Trajectory points, double start, double hold = 1, int id = 0, bool allow_outside = false) {
        const auto current = requireUsable(allow_outside);
        if (cancel.load()) throw Cancelled{};
        if (points.empty()) throw std::runtime_error("Empty trajectory");
        stream = std::make_unique<Stream>(model, current.q, mono());
        armed = true;
        step_ids.clear();
        Trajectory scheduled;
        const double origin = points.begin()->first;
        for (const auto& point : points) scheduled[start + point.first - origin] = point.second;
        hold_until = scheduled.rbegin()->first + hold;
        step_id = id;
        stream->play(std::move(scheduled), mono());
        std::lock_guard<std::mutex> lock(mutex);
        target_rpy = points.rbegin()->second;
        idle = false;
    }
    void accept(const Samples& samples, double now) {
        requireUsable();
        Trajectory fresh;
        std::uint64_t dropped = 0;
        for (const auto& sample : samples) {
            if (sample.t_s >= now) fresh[sample.t_s] = {sample.roll, sample.pitch, sample.yaw};
            else ++dropped;
        }
        if (!fresh.empty()) { hold_until = 0; step_id = 0; }
        stream->pushSamples(std::move(fresh), now, dropped);
        std::lock_guard<std::mutex> lock(mutex);
        idle = false;
    }
    void receive() {
        if (!udp) return;
        char packet[65536];
        ssize_t size;
        while ((size = recv(*udp, packet, sizeof(packet) - 1, MSG_DONTWAIT)) >= 0) {
            const double now = mono();
            packet[size] = 0;
            std::replace(packet, packet + size, ',', ' ');
            std::istringstream rows(packet);
            Samples samples;
            Sample sample;
            while (rows >> sample.t_s >> sample.roll >> sample.pitch >> sample.yaw) samples.push_back(sample);
            accept(samples, now);
        }
    }
    void tick() {
        if (clock_rate != options.at("rate")) {
            clock_rate = options.at("rate");
            cycle = std::make_unique<PeriodicClock>(clock_rate);
        }
        cycle->wait();
        checkBus();
        if (!armed) { cycle->next(); return; }
        receive();
        const double now = mono();
        const auto target = stream->step(now, now - previous, options.at("vmax"), options.at("amax"));
        if (!step_ids.empty()) {
            const auto step = step_ids.upper_bound(now);
            if (step != step_ids.begin()) step_id = std::prev(step)->second;
        }
        previous = now;
        const double comp = options.at("comp"), lead = options.at("lead");
        if (comp && !ripple) ripple = std::make_unique<RippleComp>(comp, model.ids, lead);
        if (ripple) { ripple->gain = comp; ripple->lead_s = lead / 1000; }
        CommandRow row{};
        row.plan_time = now - epoch;
        row.sample = {target.rpy, target.q, target.motor_velocity};
        probe::send("motor_targets_degrees", std::vector<double>{
            target.q[0], target.q[1], target.q[2],
            target.motor_velocity[0], target.motor_velocity[1], target.motor_velocity[2],
            options.at("kp"), options.at("kd"), comp, lead, options.at("tf")});
        row.buffer_ms = target.buffer_ms;
        row.state = target.state;
        row.step_id = step_id;
        row.dropped_samples = target.dropped_samples;
        row.last_rx_mono_s = target.last_rx_mono_s;
        row.elapsed = mono() - epoch;
        row.feedback = snapshot(model.ids);
        const auto observed_at = Clock::now();
        Vec q;
        for (int i = 0; i < 3; ++i) {
            q[i] = row.feedback[i].value[0];
            for (const auto& observed : row.feedback[i].observed)
                row.ages[i] = std::max(row.ages[i], std::chrono::duration<double, std::milli>(observed_at - observed).count());
            row.compensation[i] = sendMit(row.frame, i + 1, model.ids[i], options.at("kp"), options.at("kd"),
                                         row.sample.q[i], row.sample.velocity[i],
                                         ripple ? ripple->torque(i, row.sample.q[i], row.sample.velocity[i]) : 0,
                                         options.at("tf") * std::tanh(row.sample.velocity[i]));
        }
        writeNeckDirect(row.frame, row.compensation);
        row.timestamp = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
        if (log) log->commands.push(row);
        const Vec rpy = model.forward(q);
        {
            std::lock_guard<std::mutex> lock(mutex);
            state = State(target.state);
            measured_q = q;
            measured_rpy = rpy;
            idle = target.buffer_ms == 0 && now >= hold_until &&
                   (state == State::Waiting || state == State::Holding);
        }
        changed.notify_all();
        cycle->next();
    }
    void run() {
        for (;;) {
            std::function<void()> job;
            {
                std::unique_lock<std::mutex> lock(mutex);
                changed.wait(lock, [this] { return closing || !jobs.empty() || stream; });
                if (closing) break;
                if (!jobs.empty()) {
                    job = std::move(jobs.front());
                    jobs.pop_front();
                    --pending;
                    working = true;
                }
            }
            try {
                if (job) {
                    job();
                    std::lock_guard<std::mutex> lock(mutex);
                    working = false;
                    changed.notify_all();
                }
                if (stream && !cancel.load()) tick();
            } catch (const Cancelled&) {
                closeSession();
                std::lock_guard<std::mutex> lock(mutex);
                working = false;
                changed.notify_all();
            } catch (const std::exception& e) {
                closeSession();
                bool stopped;
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    working = false;
                    stopped = cancel.load();
                    if (!stopped) {
                        error = std::current_exception();
                        started = false;
                        jobs.clear();
                        pending = 0;
                    }
                }
                changed.notify_all();
                if (!stopped) std::cerr << "Neck controller: " << e.what() << '\n';
                if (!stopped) probe::send("neck_status_result", e.what());
            }
        }
        closeSession();
        stopBus();
    }
};

Controller::Controller() : impl(std::make_unique<Impl>()) {}
Controller::~Controller() = default;
bool Controller::start() {
    std::lock_guard<std::mutex> lock(impl->mutex);
    if (impl->started) return true;
    impl->started = true;
    impl->cancel = false;
    impl->error = nullptr;
    impl->jobs.push_back([p = impl.get()] { p->startSession(); });
    ++impl->pending;
    impl->changed.notify_all();
    return true;
}
void Controller::stop() {
    std::lock_guard<std::mutex> lock(impl->mutex);
    impl->started = false;
    impl->cancel = true;
    impl->error = nullptr;
    impl->jobs.clear();
    impl->jobs.push_back([p = impl.get()] { p->closeSession(); });
    impl->pending = 1;
    impl->changed.notify_all();
}
void Controller::release() {
    stop();
    impl->post([p = impl.get()] {
        probe::send("drive_session_events", "release");
        EtherCAT_Msg frame{};
        for (int i = 0; i < 3; ++i)
            send_motor_ctrl_cmd(&frame, i + 1, p->model.ids[i], 0, 1, 0, 0, 0);
        writeNeckDirect(frame);
        finishNeckDirect();
    });
}
void Controller::moveTo(double roll, double pitch, double yaw, double duration) {
    impl->post([p = impl.get(), target = Vec{roll, pitch, yaw}, duration] {
        p->move(p->model.inverse(target).q, target, duration);
    });
}
void Controller::moveMotors(double q1, double q2, double q3, double duration) {
    impl->post([p = impl.get(), q = Vec{q1, q2, q3}, duration] {
        p->move(q, p->model.forward(q), duration);
    });
}
void Controller::sweep(const std::string& axis, double amp, double vel, int cycles, double accel) {
    impl->post([p = impl.get(), axis, amp, vel, cycles, accel] {
        auto points = sweepTrajectory(p->model.forward(p->measured()), axis, amp, vel, cycles, accel);
        p->install(std::move(points), mono());
    });
}
void Controller::stepTest(const std::string& axis, double amp, const std::vector<double>& times, double hold) {
    impl->post([p = impl.get(), axis, amp, times, hold] {
        const auto current = p->requireUsable();
        const std::map<std::string, int> axes{{"roll", 0}, {"pitch", 1}, {"yaw", 2}};
        Vec peak{};
        peak[axes.at(axis)] = amp;
        Trajectory points{{0, current.rpy}};
        std::map<double, int> ids;
        auto append = [&](const Vec& target, double duration, double pause, int id) {
            if (!(duration > 0) || !std::isfinite(duration) || !(pause >= 0) || !std::isfinite(pause))
                throw std::runtime_error("Invalid step duration/hold");
            const double begin = points.rbegin()->first;
            ids[begin] = id;
            for (const auto& point : moveTrajectory(points.rbegin()->second, target, duration))
                points[begin + point.first] = point.second;
            for (double t = .01; t < pause; t += .01) points[begin + duration + t] = target;
            points[begin + duration + pause] = target;
        };
        append({}, defaults().at("time"), 1, 0);
        int index = 0;
        for (double duration : times) {
            ++index;
            for (int direction : {1, -1}) {
                append(direction > 0 ? peak : Vec{}, duration, hold, direction * index);
                std::cout << "NeckStepTest step_id=" << direction * index << " T=" << duration << " s"
                          << " peak_velocity=" << 1.875 * std::abs(amp) / duration << " deg/s"
                          << " peak_acceleration=" << 10 / std::sqrt(3.0) * std::abs(amp) / (duration * duration)
                          << " deg/s^2\n";
            }
        }
        ids[points.rbegin()->first] = 0;
        const double start = mono();
        p->install(std::move(points), start, 0);
        for (const auto& entry : ids) p->step_ids[start + entry.first] = entry.second;
    });
}
void Controller::playFile(const std::string& name, double transition) {
    impl->post([p = impl.get(), name, transition] {
        auto points = readTrajectory(name);
        const auto current = p->requireUsable();
        const Vec first = points.begin()->second;
        const double distance = maxAbs(first - current.rpy);
        if (distance > 1e-6) {
            if (!std::isfinite(transition) || transition <= 0)
                throw std::runtime_error("NeckPlay transition time must be positive");
            double duration = transition;
            if (p->options.at("vmax") > 0)
                duration = std::max(duration, 1.875 * distance / p->options.at("vmax"));
            if (p->options.at("amax") > 0)
                duration = std::max(duration, std::sqrt(10 / std::sqrt(3.0) * distance / p->options.at("amax")));
            auto approach = moveTrajectory(current.rpy, first, duration);
            for (const auto& point : points) approach[duration + point.first] = point.second;
            points = std::move(approach);
        }
        p->udp.reset();
        p->install(std::move(points), mono()); // 读取和规划完成后才启动轨迹时钟。
    });
}
void Controller::play(const Samples& samples) {
    const double start = mono();
    impl->post([p = impl.get(), samples, start] {
        Trajectory points;
        for (const auto& sample : samples) points[sample.t_s] = {sample.roll, sample.pitch, sample.yaw};
        p->install(std::move(points), start);
    });
}
void Controller::push(const Samples& samples) {
    const double now = mono();
    impl->post([p = impl.get(), samples, now] { p->accept(samples, now); });
}
bool Controller::listenUdp(int port) {
    auto socket = std::shared_ptr<int>(new int(::socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK, 0)),
                                     [](int* fd) { close(*fd); delete fd; });
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(port);
    if (bind(*socket, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) return false;
    impl->post([p = impl.get(), socket] {
        const auto current = p->requireUsable();
        p->stream = std::make_unique<Stream>(p->model, current.q, mono());
        p->armed = true;
        p->udp = socket;
        p->stream->listen();
        p->hold_until = 0;
        p->step_id = 0;
    });
    return true;
}
void Controller::stopUdp() {
    impl->post([p = impl.get()] {
        p->udp.reset();
        if (p->stream) p->stream->hold(mono());
        p->hold_until = 0;
    });
}
void Controller::setParams(double kp, double kd, double comp, double lead, double tf) {
    impl->post([p = impl.get(), kp, kd, comp, lead, tf] {
        p->options["kp"] = kp;
        p->options["kd"] = kd;
        p->options["comp"] = comp;
        p->options["lead"] = lead;
        p->options["tf"] = tf;
    });
}
void Controller::setRate(double rate) {
    impl->post([p = impl.get(), rate] { p->options["rate"] = rate; });
}
void Controller::setLimits(double vmax, double amax) {
    impl->post([p = impl.get(), vmax, amax] {
        p->options["vmax"] = vmax;
        p->options["amax"] = amax;
    });
}
void Controller::waitIdle() {
    std::unique_lock<std::mutex> lock(impl->mutex);
    impl->changed.wait(lock, [this] { return !impl->pending && !impl->working && impl->idle; });
    if (impl->error) std::rethrow_exception(impl->error);
}
Status Controller::status() const {
    const auto probe_status = poseStatus(impl->model, freshMotors(impl->model.ids));
    probe::send("neck_status_result", probe_status.detail);
    return probe_status;
}
State Controller::state() const {
    std::lock_guard<std::mutex> lock(impl->mutex);
    return impl->state;
}
std::array<double, 3> Controller::rpy() const {
    std::lock_guard<std::mutex> lock(impl->mutex);
    return impl->measured_rpy;
}
std::array<double, 3> Controller::targetRpy() const {
    std::lock_guard<std::mutex> lock(impl->mutex);
    return impl->target_rpy;
}
std::array<double, 3> Controller::motors() const {
    std::lock_guard<std::mutex> lock(impl->mutex);
    return impl->measured_q;
}
} // namespace neck
