#ifndef BMS_APP_H
#define BMS_APP_H
#include "bq76922.h"

typedef struct {
    uint32_t step7_2;
    uint32_t step7_3;
    uint32_t all_on;
    uint32_t off;
    uint32_t configure;
    int32_t motor1_permille;
    int32_t motor2_permille;
    uint32_t ai_alarm;
    uint32_t motor_on;
    uint32_t motor_off;
} BMS_Control;
typedef struct {
    uint32_t heartbeat, sample_count, error_count, sample_tick;
    uint32_t stage;
    uint32_t last_action;
    uint32_t action_count;
    int sample_result, configure_result, fet_on_result, fet_off_result;
    uint16_t device_id;
    uint8_t data_valid, setup_ok, estop_ok, converter_pg;
    uint8_t motor_fault_mask, power_gpio_mask;
    BQ_Data sensor;
    int32_t remote_motor_result;
} BMS_Status;
extern volatile BMS_Control g_control;
extern volatile BMS_Status g_bms;
typedef struct {
    uint32_t start, stop;
    uint32_t duration_ms;
    uint32_t running;
    uint32_t selected_cell;
    uint32_t requested_mask;
    uint32_t active_mask;
    uint32_t elapsed_s, remaining_ms, started_tick, status_tick;
    uint32_t command_count;
    int32_t command_result, status_result, result;
    uint32_t config, interval_s, activation_seen, stop_pending;
    uint32_t enable;
} BMS_Balance;
extern volatile BMS_Balance g_balance;
typedef struct {
    uint32_t on, off;
    uint32_t stat1_n, stat2_n, pg_n;
    uint32_t chgon, chgfn, chgpg;
    uint32_t charging, complete;
    uint32_t state;
    uint32_t command_enabled;
    int32_t result;
    uint32_t sample_tick;
} BMS_Charge;
extern volatile BMS_Charge g_charge;
void BMS_App_Init(I2C_HandleTypeDef *bus);
void BMS_App_Update(void);
int BMS_RemoteMotorOn(void);
#endif
