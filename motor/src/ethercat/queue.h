//
// Created by bismarck on 11/18/22.
//

#ifndef SOEM_QUEUE_H
#define SOEM_QUEUE_H

#include <boost/lockfree/spsc_queue.hpp>
#include <memory>
#include <array>
#include <atomic>
#include <thread>
#include <cstring>

extern "C" {
#include "transmit.h"
};

using boost::lockfree::spsc_queue, boost::lockfree::capacity;
typedef std::shared_ptr<EtherCAT_Msg> EtherCAT_Msg_ptr;

typedef struct
{
    int passage;
    Motor_Msg motor;
    uint8_t can_ide = 0;
    int count = 1;
    bool stream = false; // 轨迹点不使用普通命令的 10 周期等待
    Motor_Msg following[2]{}; // 可选：同一 CAN 上后续两个通道的成组目标
    std::array<double, 3> compensation{};
} Queue_Msg;

typedef std::shared_ptr<Queue_Msg> Queue_Msg_ptr;

extern spsc_queue<Queue_Msg_ptr, capacity<10>> messages[SLAVE_NUMBER];
extern std::atomic<bool> running;
extern std::thread runThread;

bool queryMotor(int slave, int passage, const Motor_Msg& request, Motor_Msg& reply);
inline float motorParameterValue(const Motor_Msg& reply) {
    const uint32_t bits = uint32_t(reply.data[2]) << 24 | uint32_t(reply.data[3]) << 16 |
                          uint32_t(reply.data[4]) << 8 | reply.data[5];
    float value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

#endif //SOEM_QUEUE_H
