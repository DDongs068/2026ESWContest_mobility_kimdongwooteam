#ifndef BMS_APP_H
#define BMS_APP_H
#include "bq76922.h"

/* Write 1 to a trigger. It is cleared before its action is attempted. */
typedef struct {
    uint32_t step7_2;          /* BMIC FET command only. */
    uint32_t step7_3;          /* BMS_ALLOW HIGH only. */
    uint32_t all_on;           /* After step 7: converter + two motors only. */
    uint32_t off;              /* MCU outputs OFF, then one BMIC OFF command. */
    uint32_t configure;        /* Outputs OFF, repeat measurement setup. */
    int32_t motor1_permille;   /* -1000..1000; boot +900 = 90%. */
    int32_t motor2_permille;
    uint32_t ai_alarm;         /* Nonzero: AI_LED ON + hold full OFF, block ONs. */
    uint32_t motor_on;         /* Same load sequence as all_on; FETs untouched. */
    uint32_t motor_off;        /* PWM, MOTOR_CMD, DCDC_CMD OFF; BMS stays as-is. */
} BMS_Control;
typedef struct {
    uint32_t heartbeat, sample_count, error_count, sample_tick;
    uint32_t stage;            /* 0=loads OFF, 1=rail wait, 2=motor wake, 3=PWM. */
    uint32_t last_action;      /* 0=OFF, 3=configure, 72/73=FET, 100/101=motor, 200=AI OFF. */
    uint32_t action_count;
    int sample_result, configure_result, fet_on_result, fet_off_result;
    uint16_t device_id;
    uint8_t data_valid, setup_ok, estop_ok, converter_pg;
    uint8_t motor_fault_mask, power_gpio_mask;
    BQ_Data sensor;
    int32_t remote_motor_result; /* 1=queued, 0=applied; -10..-16=remote ON rejected. */
} BMS_Status;
extern volatile BMS_Control g_control;
extern volatile BMS_Status g_bms;
typedef struct {
    uint32_t start, stop;       /* Write1: one-shot request, then auto-clear. */
    uint32_t duration_ms;       /* Default0=continuous monitoring; optional session limit. */
    uint32_t running;           /* Host request active; see active_mask for BMIC. */
    uint32_t selected_cell;     /* Display cell1..4, highest at start. */
    uint32_t requested_mask;    /* Physical channels1,2,3,5 => bits0,1,2,4. */
    uint32_t active_mask;       /* Actual CB_ACTIVE_CELLS, never synthesized. */
    uint32_t elapsed_s, remaining_ms, started_tick, status_tick;
    uint32_t command_count;
    int32_t command_result, status_result, result;
    uint32_t config, interval_s, activation_seen, stop_pending;
    uint32_t enable;            /* Default1=automatic highest-cell balancing. 0=stop. */
} BMS_Balance;
extern volatile BMS_Balance g_balance;
typedef struct {
    uint32_t on, off;          /* One-shot CE permission requests; defaultOFF. */
    uint32_t stat1_n, stat2_n, pg_n;
    uint32_t chgon, chgfn, chgpg; /* Actual active-low BQ24610 outputs/LED signals. */
    uint32_t charging, complete;
    uint32_t state;            /* 0=no input,1=charging,2=complete,3=idle,4=invalid. */
    uint32_t command_enabled;  /* Actual MCU CHG_CMD output; not proof of current. */
    int32_t result;            /* 0=OK, negative=guard/measurement error. */
    uint32_t sample_tick;
} BMS_Charge;
extern volatile BMS_Charge g_charge;
void BMS_App_Init(I2C_HandleTypeDef *bus);
void BMS_App_Update(void);
/* Standalone Pi ON: request existing FET -> BMS_ALLOW -> motor sequence. */
int BMS_RemoteMotorOn(void);
#endif
