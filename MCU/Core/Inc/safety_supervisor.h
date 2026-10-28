#ifndef SAFETY_SUPERVISOR_H
#define SAFETY_SUPERVISOR_H
#include <stdint.h>
#include "bq76922.h"
/* Independent live BMIC safety. Replay values never enter this module. */
enum { SAFE_BMIC=1u, SAFE_COMM=2u, SAFE_STALE=4u, SAFE_EST0P=8u,
       SAFE_SENSOR=16u, SAFE_DRIVER=32u, SAFE_DSG=64u, SAFE_CONFIG=128u, SAFE_PROFILE=256u };
typedef struct {
    uint32_t active, latched, event_count, first_trip_tick, last_tick, ack;
    uint32_t sample_age_ms;
    uint8_t safety[3], pf[4], fet;
    int32_t last_rearm_result, profile_result;
    uint32_t profile_valid, profile_tick;
    BQ_ProtectionProfile profile;
} Safety_Status;
extern volatile Safety_Status g_safety;
void Safety_Init(void);
void Safety_RefreshProfile(void);
/* Run before AND after BMS_App_Update; finite I2C timeouts remain in BMIC driver. */
void Safety_Update(void);
int Safety_Rearm(void);
#endif
