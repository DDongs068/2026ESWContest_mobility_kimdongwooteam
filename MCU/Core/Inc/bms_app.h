#ifndef BMS_APP_H
#define BMS_APP_H
#include "bq76922.h"

typedef struct {
    uint32_t fet_on;
    uint32_t motor_on;
    uint32_t charge_on;
} BMS_Control;
extern volatile BMS_Control g_control;

#define BMS_MOTOR_DIRECT_DC_LINK 1
#define BMS_MOTOR1_PERMILLE 900
#define BMS_MOTOR2_PERMILLE 300
#define BMS_SAMPLE_PERIOD_MS 100u

typedef struct {
    uint32_t heartbeat, sample_count, sample_attempt_count, error_count, sample_tick;
    uint32_t session_sample_start;
    uint32_t stage;
    uint32_t fet_state;
    uint32_t fet_enabled;
    uint32_t ai_alarm;
    uint32_t last_action, action_count;
    int sample_result, configure_result, fet_on_result, fet_off_result;
    int32_t motor_result;
    uint16_t device_id;
    uint8_t data_valid, setup_ok, estop_ok, converter_pg;
    uint8_t motor_fault_mask, power_gpio_mask;
    BQ_Data sensor;
} BMS_Status;
extern volatile BMS_Status g_bms;

typedef struct {
    uint32_t running, selected_cell, requested_mask, active_mask;
    uint32_t elapsed_s, started_tick, status_tick, command_count;
    uint32_t config, interval_s, activation_seen, stop_pending;
    int32_t command_result, status_result, result;
} BMS_Balance;
extern volatile BMS_Balance g_balance;

typedef struct {
    uint32_t stat1_n, stat2_n, pg_n, chgon, chgfn, chgpg;
    uint32_t charging, complete, state;
    uint32_t command_enabled;
    int32_t result;
    uint32_t sample_tick;
} BMS_Charge;
extern volatile BMS_Charge g_charge;

typedef enum { BMS_OUTPUT_FET, BMS_OUTPUT_MOTOR, BMS_OUTPUT_CHARGE } BMS_Output;
void BMS_App_Init(I2C_HandleTypeDef *bus);
void BMS_App_Update(void);

int BMS_SetStarted(int started);

int BMS_RequestOutput(BMS_Output output, int on);
void BMS_StopAllOutputs(void);
#endif
