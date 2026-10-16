extern "C" {
#include "ethercat.h"
#include "protocol/motor_control.h"
#include "transmit.h"
}

#include "queue.h"
#include "neck/neck_sweep.h"
#include "periodic_clock.h"
#include <sys/time.h>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include "time.h"

spsc_queue<Queue_Msg_ptr, capacity<10>> messages[SLAVE_NUMBER];
std::atomic<bool> running{false};
std::thread runThread;

namespace {
std::mutex direct_mutex;
EtherCAT_Msg direct_frame{};
std::array<double, 3> direct_compensation{}, sent_compensation{};
bool direct_active = false, direct_pending = false;
std::mutex query_mutex, query_serial;
std::condition_variable query_changed;
struct {
    bool active = false, sent = false, done = false;
    int slave = 0, passage = 1;
    Motor_Msg request{}, reply{};
} query;
}
bool queryMotor(int slave, int passage, const Motor_Msg& request, Motor_Msg& reply) {
    if (!running || slave < 0 || slave >= SLAVE_NUMBER || slave >= ec_slavecount ||
        passage < 1 || passage > 6) return false;
    std::lock_guard<std::mutex> serial(query_serial);
    std::unique_lock<std::mutex> lock(query_mutex);
    query = {true, false, false, slave, passage, request, {}};
    const bool ok = query_changed.wait_for(lock, std::chrono::seconds(1), [] { return query.done; });
    if (ok) reply = query.reply;
    query.active = false;
    return ok;
}
void writeNeckDirect(const EtherCAT_Msg& frame, const std::array<double, 3>& compensation) {
    std::lock_guard<std::mutex> lock(direct_mutex);
    direct_frame = frame;
    direct_compensation = compensation;
    direct_active = direct_pending = true;
}
bool neckDirectPending() {
    std::lock_guard<std::mutex> lock(direct_mutex);
    return direct_pending;
}
void finishNeckDirect() {
    std::lock_guard<std::mutex> lock(direct_mutex);
    direct_active = false; // 尚未发送的最后一帧仍由下一次 PDO 刷新接走。
}


char IOmap[4096];
std::thread checkThread;
std::atomic<bool> checking{false};
int expectedWKC;
boolean needlf;
std::atomic<int> wkc{0};
std::atomic<bool> inOP{false};
uint8 currentgroup = 0;
bool isConfig[SLAVE_NUMBER]{false};

#define EC_TIMEOUTMON 500

void EtherCAT_Data_Get();

void EtherCAT_Command_Set();

static void degraded_handler()
{
    neck_bus_ready = false;
    printf("[EtherCAT Error] Logging error...\n");
    time_t current_time = time(NULL);
    char* time_str = ctime(&current_time);
    printf("ESTOP. EtherCAT became degraded at %s.\n", time_str);
    printf("[EtherCAT Error] Stopping RT process.\n");
}

void EtherCAT_Init_Failure(const char* step)
{
    if (!inOP && ec_slavecount > 0) ec_readstate();
    flockfile(stdout);
    printf("[EtherCAT Init] step=%s slaves=%d states=[", step, ec_slavecount);
    for (int slave = 1; slave <= ec_slavecount; ++slave)
        printf("%s%d:0x%02x/AL=0x%04x", slave == 1 ? "" : ", ", slave,
               ec_slave[slave].state, ec_slave[slave].ALstatuscode);
    printf("] WKC=%d/%d\n", wkc.load(), expectedWKC);
    funlockfile(stdout);
}

static int run_ethercat(const char* ifname)
{
    int chk;
    needlf = FALSE;
    inOP = FALSE;
    ec_slavecount = expectedWKC = 0;
    wkc = 0;

    /* initialise SOEM, bind socket to ifname */
    if (ec_init(ifname))
    {
        printf("[EtherCAT Init] Initialization on device %s succeeded.\n", ifname);
        /* find and auto-config slaves */

        if (ec_config_init(FALSE) > 0)
        {
            printf("[EtherCAT Init] %d slaves found and configured.\n", ec_slavecount);
            if (ec_slavecount < SLAVE_NUMBER)
            {
                printf("[RT EtherCAT] Warning: Expected %d slaves, found %d.\n", SLAVE_NUMBER, ec_slavecount);
            }

            for (int slave_idx = 0; slave_idx < ec_slavecount; slave_idx++)
                ec_slave[slave_idx + 1].CoEdetails &= ~ECT_COEDET_SDOCA;

            if (ec_config_map(&IOmap) <= 0)
            {
                EtherCAT_Init_Failure("map PDO");
                return 0;
            }
            if (!ec_configdc())
                EtherCAT_Init_Failure("configure DC: no DC slaves; continuing");

            printf("[EtherCAT Init] Mapped slaves.\n");
            /* wait for all slaves to reach SAFE_OP state */
            if (ec_statecheck(0, EC_STATE_SAFE_OP, EC_TIMEOUTSTATE * SLAVE_NUMBER) != EC_STATE_SAFE_OP)
            {
                EtherCAT_Init_Failure("wait SAFE_OP timeout; retry");
                return 0;
            }

            for (int slave_idx = 0; slave_idx < ec_slavecount; slave_idx++)
            {
                printf("[SLAVE %d]\n", slave_idx);
                printf("  IN  %d bytes, %d bits\n", ec_slave[slave_idx].Ibytes, ec_slave[slave_idx].Ibits);
                printf("  OUT %d bytes, %d bits\n", ec_slave[slave_idx].Obytes, ec_slave[slave_idx].Obits);
                printf("\n");
            }

            printf("[EtherCAT Init] segments : %d : %d %d %d %d\n", ec_group[0].nsegments, ec_group[0].IOsegment[0],
                   ec_group[0].IOsegment[1], ec_group[0].IOsegment[2], ec_group[0].IOsegment[3]);

            printf("[EtherCAT Init] Requesting operational state for all slaves...\n");
            expectedWKC = (ec_group[0].outputsWKC * 2) + ec_group[0].inputsWKC;
            printf("[EtherCAT Init] Calculated workcounter %d\n", expectedWKC);
            if (ec_send_processdata() <= 0)
                EtherCAT_Init_Failure("send initial PDO");
            wkc = ec_receive_processdata(EC_TIMEOUTRET);
            if (wkc < expectedWKC)
                EtherCAT_Init_Failure("receive initial PDO; waiting for OP");
            startRun(); // 此后 PDO 只由收发线程处理，等待 OP 时也不中断。
            for (;;)
            {
                ec_slave[0].state = EC_STATE_OPERATIONAL;
                if (ec_writestate(0) <= 0)
                    EtherCAT_Init_Failure("request OP");
                chk = 40;
                do
                {
                    ec_statecheck(0, EC_STATE_OPERATIONAL, 50000);
                }
                while (chk-- && (ec_slave[0].state != EC_STATE_OPERATIONAL));

                if (ec_slave[0].state == EC_STATE_OPERATIONAL)
                {
                    ec_readstate(); // 同步各从站状态缓存。
                    printf("[EtherCAT Init] Operational state reached for all slaves.\n");
                    inOP = TRUE;
                    return 1;
                }
                EtherCAT_Init_Failure("wait OP timeout; retry");
            }
        }
        else
        {
            EtherCAT_Init_Failure("discover/configure slaves");
        }
    }
    else
    {
        EtherCAT_Init_Failure("open socket");
    }
    return 0;
}

static int err_count = 0;
static int err_iteration_count = 0;
/**@brief EtherCAT errors are measured over this period of loop iterations */
#define K_ETHERCAT_ERR_PERIOD 100

/**@brief Maximum number of etherCAT errors before a fault per period of loop iterations */
#define K_ETHERCAT_ERR_MAX 20

static void ecatcheck()
{
    int slave = 0;
    while (checking)
    {
        // count errors
        if (err_iteration_count > K_ETHERCAT_ERR_PERIOD)
        {
            err_iteration_count = 0;
            err_count = 0;
        }

        if (err_count > K_ETHERCAT_ERR_MAX)
        {
            // possibly shut down
            printf("[EtherCAT Error] EtherCAT connection degraded.\n");
            printf("[Simulink-Linux] Shutting down....\n");
            degraded_handler();
            break;
        }
        err_iteration_count++;

        if (inOP && ((wkc < expectedWKC) || ec_group[currentgroup].docheckstate))
        {
            if (needlf)
            {
                needlf = FALSE;
                printf("\n");
            }
            /* one ore more slaves are not responding */
            ec_group[currentgroup].docheckstate = FALSE;
            ec_readstate();
            for (slave = 1; slave <= ec_slavecount; slave++)
            {
                if ((ec_slave[slave].group == currentgroup) && (ec_slave[slave].state != EC_STATE_OPERATIONAL))
                {
                    ec_group[currentgroup].docheckstate = TRUE;
                    if (ec_slave[slave].state == (EC_STATE_SAFE_OP + EC_STATE_ERROR))
                    {
                        printf("[EtherCAT Error] Slave %d is in SAFE_OP + ERROR, attempting ack.\n", slave);
                        ec_slave[slave].state = (EC_STATE_SAFE_OP + EC_STATE_ACK);
                        ec_writestate(slave);
                        err_count++;
                    }
                    else if (ec_slave[slave].state == EC_STATE_SAFE_OP)
                    {
                        printf("[EtherCAT Error] Slave %d is in SAFE_OP, change to OPERATIONAL.\n", slave);
                        ec_slave[slave].state = EC_STATE_OPERATIONAL;
                        ec_writestate(slave);
                        err_count++;
                    }
                    else if (ec_slave[slave].state > 0)
                    {
                        if (ec_reconfig_slave(slave, EC_TIMEOUTMON))
                        {
                            ec_slave[slave].islost = FALSE;
                            printf("[EtherCAT Status] Slave %d reconfigured\n", slave);
                        }
                    }
                    else if (!ec_slave[slave].islost)
                    {
                        /* re-check state */
                        ec_statecheck(slave, EC_STATE_OPERATIONAL, EC_TIMEOUTRET);
                        if (!ec_slave[slave].state)
                        {
                            ec_slave[slave].islost = TRUE;
                            printf("[EtherCAT Error] Slave %d lost\n", slave);
                            err_count++;
                        }
                    }
                }
                if (ec_slave[slave].islost)
                {
                    if (!ec_slave[slave].state)
                    {
                        if (ec_recover_slave(slave, EC_TIMEOUTMON))
                        {
                            ec_slave[slave].islost = FALSE;
                            printf("[EtherCAT Status] Slave %d recovered\n", slave);
                        }
                    }
                    else
                    {
                        ec_slave[slave].islost = FALSE;
                        printf("[EtherCAT Status] Slave %d found\n", slave);
                    }
                }
            }
            if (!ec_group[currentgroup].docheckstate)
                printf("[EtherCAT Status] All slaves resumed OPERATIONAL.\n");
        }
        osal_usleep(50000);
    }
}

void EtherCAT_Init(char* ifname)
{
    int i;
    int rc;
    printf("[EtherCAT] Initializing EtherCAT\n");
    for (i = 1; i < 10; i++)
    {
        printf("[EtherCAT] Attempting to start EtherCAT, try %d of 10.\n", i);
        rc = run_ethercat(ifname);
        if (rc)
            break;
        osal_usleep(1000000);
    }
    if (rc)
        printf("[EtherCAT] EtherCAT successfully initialized on attempt %d \n", i);
    else
    {
        EtherCAT_Init_Failure("initialize: retries exhausted");
    }
}

void EtherCAT_Transmit(EtherCAT_Msg* MasterCommand)
{
    for (int i = 0; i < ec_slavecount; i++)
    {
        memcpy((void*)(ec_slave[0].outputs + i * sizeof(EtherCAT_Msg)), (void*)&(MasterCommand[i]),
               sizeof(EtherCAT_Msg));
    }
    ec_send_processdata();
}

static int wkc_err_count = 0;
static int wkc_err_iteration_count = 0;

//数组大小根据从站数量确定
EtherCAT_Msg Rx_Message[SLAVE_NUMBER];
EtherCAT_Msg Tx_Message[SLAVE_NUMBER];

OD_Motor_Msg Rx_Motor_Msg[SLAVE_NUMBER][6];
/**
 * @description:
 * @return {*}
 */
void EtherCAT_Run()
{
    if (wkc_err_iteration_count > K_ETHERCAT_ERR_PERIOD)
    {
        wkc_err_count = 0;
        wkc_err_iteration_count = 0;
    }
    if (wkc_err_count > K_ETHERCAT_ERR_MAX)
    {
        printf("[EtherCAT Error] Error count too high!\n");
        degraded_handler();
    }
    // send
    EtherCAT_Command_Set();
    ec_send_processdata();
    // receive
    wkc = ec_receive_processdata(EC_TIMEOUTRET);
    neck_bus_ready = inOP && ec_slavecount > 0 && ec_slavecount <= SLAVE_NUMBER &&
                     wkc >= expectedWKC && ec_slave[1].state == EC_STATE_OPERATIONAL &&
                     ec_slave[1].Obytes == sizeof(EtherCAT_Msg) &&
                     ec_slave[1].Ibytes >= sizeof(EtherCAT_Msg) &&
                     ec_slave[1].outputs && ec_slave[1].inputs;
    EtherCAT_Data_Get();
    //  check for dropped packet
    if (wkc < expectedWKC)
    {
        printf("\x1b[31m[EtherCAT Error] Dropped packet (Bad WKC!)\x1b[0m\n");
        wkc_err_count++;
    }
    else
    {
        needlf = TRUE;
    }
    wkc_err_iteration_count++;
}

/**
 * @description: slave data get
 * @return {*}
 * @author: Kx Zhang
 */
void EtherCAT_Data_Get()
{
    if (wkc < expectedWKC) return; // 不把丢包时缓存的 PDO 当作新反馈。
    for (int slave = 0; slave < ec_slavecount && slave < SLAVE_NUMBER; ++slave)
    {
        EtherCAT_Msg* slave_src = (EtherCAT_Msg*)(ec_slave[slave + 1].inputs);
        if (!slave_src || ec_slave[slave + 1].Ibytes < sizeof(EtherCAT_Msg)) continue;
        Rx_Message[slave] = *slave_src;
        {
            std::lock_guard<std::mutex> lock(query_mutex);
            if (query.active && query.sent && query.slave == slave) {
                for (const auto& frame : Rx_Message[slave].motor) {
                    if (frame.id != query.request.id) continue;
                    const bool match = frame.id == 0x7ff
                        ? frame.dlc >= 5 && frame.data[2] == 1 &&
                          ((frame.data[0] == 0xff && frame.data[1] == 0xff) ||
                           (frame.data[0] == 0x80 && frame.data[1] == 0x80))
                        : frame.dlc == 6 && (frame.data[0] >> 5) == 5 &&
                          frame.data[1] == query.request.data[1];
                    if (match) {
                        query.reply = frame;
                        query.done = true;
                        query.active = false;
                        query_changed.notify_all();
                        break;
                    }
                }
            }
        }

        RV_can_data_repack(&Rx_Message[slave], comm_ack, Rx_Motor_Msg[slave], slave, isConfig[slave]);
        if (slave == 0 && neck_bus_ready.load())
            recordNeckFeedback(Rx_Message[slave], Rx_Motor_Msg[slave], Tx_Message[slave], sent_compensation);

        if (isConfig[slave])
        {
            isConfig[slave] = false;
        }
    }
}

/**
 * @description: slave command set
 * @return {*}
 * @author: Kx Zhang
 */
// 使用命令行控制电机的例程
void EtherCAT_Command_Set()
{
    static int state[SLAVE_NUMBER];
    for (int slave = 0; slave < ec_slavecount && slave < SLAVE_NUMBER; ++slave)
    {
        bool direct = false;
        if (slave == 0) {
            std::lock_guard<std::mutex> lock(direct_mutex);
            direct = direct_active || direct_pending;
            if (direct) {
                Tx_Message[slave].can_ide = direct_frame.can_ide;
                for (int k = 0; k < 3; ++k) Tx_Message[slave].motor[k] = direct_frame.motor[k];
                sent_compensation = direct_compensation;
                direct_pending = false;
            }
        }
        Queue_Msg_ptr msg;
        if (direct) state[slave] = 0;
        else if (state[slave] == 0)
        {
            if (messages[slave].pop(msg))
            {
                if (Tx_Message[slave].can_ide != msg->can_ide) Tx_Message[slave] = {};
                Tx_Message[slave].can_ide = msg->can_ide;
                Tx_Message[slave].motor[msg->passage - 1] = msg->motor;
                for (int k = 1; k < msg->count; ++k)
                    Tx_Message[slave].motor[msg->passage + k - 1] = msg->following[k - 1];
                if (slave == 0) for (int k = 0; k < msg->count; ++k) {
                    const int slot = msg->passage - 1 + k;
                    if (slot < 3) sent_compensation[slot] = msg->compensation[k];
                }
                state[slave] = msg->stream ? 0 : 1;
            }
        }
        else if (state[slave]++ == 10)
        {
            isConfig[slave] = true;
            state[slave] = 0;
        }

        EtherCAT_Msg output = Tx_Message[slave];
        {
            std::lock_guard<std::mutex> lock(query_mutex);
            if (query.active && query.slave == slave) {
                output = {}; // 查询帧不能混入 can_ide=1 的混合控制报文。
                output.motor[query.passage - 1] = query.request;
                query.sent = true;
            }
        }
        EtherCAT_Msg* slave_dest = (EtherCAT_Msg*)(ec_slave[slave + 1].outputs);
        if (slave_dest && ec_slave[slave + 1].Obytes == sizeof(EtherCAT_Msg))
            *slave_dest = output;
    }
}

void runImpl()
{
    PeriodicClock cycle(1000);
    while (running)
    {
        cycle.wait();
        if (inOP) EtherCAT_Run();
        else { // 启动阶段仅交换 PDO，不累计运行期 WKC 故障。
            ec_send_processdata();
            wkc = ec_receive_processdata(EC_TIMEOUTRET);
        }
        cycle.next();
    }
}


void startRun()
{
    running = true;
    checking = true;
    runThread = std::thread(runImpl);
    checkThread = std::thread(ecatcheck);
}

int startBus()
{
    if (running) return 1;
    EtherCAT_Init((char*)"enp4s0");
    return inOP ? 1 : 0;
}

void stopBus()
{
    if (!running.exchange(false)) return;
    checking = false;
    runThread.join();
    checkThread.join();
    inOP = FALSE;
    neck_bus_ready = false;
    ec_close();
}
