#ifndef BMS_DEMO_H
#define BMS_DEMO_H

#include <stdint.h>

typedef struct {
    uint32_t started, active, emergency, fault, algorithm_available;
    uint32_t accepted, rejected, duplicates;
    uint32_t last_sequence, source_ms, last_rx_tick;
    int32_t decision_result, last_result;
    int32_t cell_mV[4], temp_mC[4];
    int32_t current_mA, stack_mV, pack_mV, ld_mV;
    uint32_t fault_bits;
    uint32_t first_fault_ms;
} BMS_DemoStatus;

extern volatile BMS_DemoStatus g_demo;

void BMS_DemoInit(void);

void BMS_DemoSetRunning(int running);

void BMS_DemoClear(void);

int BMS_DemoReceive(const char *csv);

#endif
