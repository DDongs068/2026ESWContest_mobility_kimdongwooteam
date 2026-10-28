#ifndef DEMO_STREAM_H
#define DEMO_STREAM_H
#include "bms_app.h"
#define DEMO_WIRE_SIZE 72u
#define DEMO_QUEUE_SIZE 16u
typedef struct {
    uint32_t mode, session, accepted, played, queued, last_sequence, source_ms;
    uint32_t rejected, duplicates, underruns, baseline_flags, spec_overcharge;
    uint32_t rule_ready, route, valid, first_baseline_ms;
    int32_t last_result;
} Demo_Status;
extern volatile Demo_Status g_demo;
void Demo_Init(void);
void Demo_Update(void);
int Demo_Start(uint32_t session);
void Demo_Stop(void);
int Demo_ReceiveHex(const char *hex);
int Demo_Sample(BMS_Status *sample, uint32_t *balance_mask);
#endif
