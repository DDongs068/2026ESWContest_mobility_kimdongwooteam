#include "bms_app.h"
#include "main.h"
#include <math.h>

volatile BMS_Control g_control;
volatile BMS_Status g_bms;
volatile BMS_Balance g_balance;
volatile BMS_Charge g_charge;
static uint32_t balance_last_command, balance_last_poll, balance_monitor_until, balance_last_attempt;
static uint32_t last_sample, phase_tick;
static uint8_t ai_stop_applied, remote_motor_start;

static int32_t Clamp(int32_t value)
{
    return value > 1000 ? 1000 : value < -1000 ? -1000 : value;
}
static int ProtectionFault(void)
{
    /* BQ76922 SLUUCG7, Safety/PF Status registers (not Alert registers).
     * Ignore reserved bits. This indication never changes power outputs. */
    return (g_bms.sensor.safety_status[0] & 0xFCu) ||
           (g_bms.sensor.safety_status[1] & 0xF7u) ||
           (g_bms.sensor.safety_status[2] & 0xF6u) ||
           (g_bms.sensor.pf_status[0] & 0xDFu) ||
           (g_bms.sensor.pf_status[1] & 0x9Fu) ||
           g_bms.sensor.pf_status[2] ||
           (g_bms.sensor.pf_status[3] & 0x01u);
}
static int BalanceAllowed(void)
{
    uint32_t now = HAL_GetTick();
    if (!g_bms.setup_ok || !g_bms.data_valid || g_bms.sample_result ||
        now-g_bms.sample_tick > 500u) return -20;
    if (g_control.ai_alarm || ProtectionFault()) return -21;
    if (g_bms.power_gpio_mask || g_bms.stage || !g_bms.estop_ok ||
        g_charge.command_enabled) return -22;
    if (g_bms.sensor.ntc_valid_mask != 15u ||
        !isfinite(g_bms.sensor.current_A) || fabsf(g_bms.sensor.current_A)>0.1f)
        return -23;
    for (unsigned i=0;i<4u;++i) {
        float v=g_bms.sensor.cell_V[i], t=g_bms.sensor.temp_C[i];
        /* User-selected balancing floor: stop below2.7V per cell. */
        if (!isfinite(v) || v<2.7f || v>4.2f ||
            !isfinite(t) || t<0.0f || t>45.0f) return -23;
    }
    return 0;
}
static void BalanceStop(int reason)
{
    g_balance.running=0; g_balance.remaining_ms=0; g_balance.requested_mask=0;
    g_balance.command_result=BQ_BalanceSet(0u);
    ++g_balance.command_count;
    balance_last_command=HAL_GetTick();
    g_balance.stop_pending=g_balance.command_result!=0;
    g_balance.result=g_balance.command_result?g_balance.command_result:reason;
    balance_monitor_until=balance_last_command+2000u;
}
static void BalanceUpdate(int cancelled)
{
    uint32_t now=HAL_GetTick();
    int force=0;
    if (g_balance.stop || cancelled) {
        int requested=g_balance.stop;
        g_balance.start=g_balance.stop=0;
        g_balance.enable=0;
        if (requested || g_balance.running)
            BalanceStop(cancelled?-21:0);
    }
    if (g_balance.start) {
        g_balance.start=0; g_balance.enable=1; force=1;
    }
    if (!g_balance.enable && g_balance.running) BalanceStop(0);
    if (g_balance.stop_pending && now-balance_last_command>=500u) BalanceStop(-21);
    if (g_balance.running) {
        int allowed=BalanceAllowed();
        uint32_t elapsed=now-g_balance.started_tick;
        float lowest=g_bms.sensor.cell_V[0];
        for (unsigned i=1;i<4u;++i)
            if (g_bms.sensor.cell_V[i]<lowest) lowest=g_bms.sensor.cell_V[i];
        if (allowed) BalanceStop(allowed);
        else if (g_balance.duration_ms && elapsed>=g_balance.duration_ms) {
            g_balance.enable=0; BalanceStop(0);
        } else if (g_bms.sensor.cell_V[g_balance.selected_cell-1u]-lowest<=0.010f)
            BalanceStop(0);
        else {
            uint32_t refresh=g_balance.interval_s*500u;
            if (refresh>5000u) refresh=5000u;
            g_balance.remaining_ms=g_balance.duration_ms?g_balance.duration_ms-elapsed:0u;
            if (now-balance_last_command>=refresh) {
                g_balance.command_result=BQ_BalanceSet((uint16_t)g_balance.requested_mask);
                ++g_balance.command_count; balance_last_command=HAL_GetTick();
                if (g_balance.command_result) BalanceStop(g_balance.command_result);
            }
        }
    }
    if (g_balance.enable && !g_balance.running && !g_balance.stop_pending &&
        !g_balance.active_mask && (force || now-balance_last_attempt>=1000u)) {
        uint8_t config=0, interval=0;
        unsigned highest=0; float lowest=g_bms.sensor.cell_V[0];
        balance_last_attempt=now;
        for (unsigned i=1;i<4u;++i) {
            if (g_bms.sensor.cell_V[i]>g_bms.sensor.cell_V[highest]) highest=i;
            if (g_bms.sensor.cell_V[i]<lowest) lowest=g_bms.sensor.cell_V[i];
        }
        if ((g_balance.result=BalanceAllowed())==0) {
            if (g_bms.sensor.cell_V[highest]-lowest<0.020f) g_balance.result=-25;
            else {
                g_balance.command_result=BQ_BalanceConfig(&config,&interval);
                g_balance.config=config; g_balance.interval_s=interval;
                if (g_balance.command_result) g_balance.result=g_balance.command_result;
                else if ((config&0x13u) || !interval) g_balance.result=-24;
                else {
                    const uint16_t masks[4]={1u,2u,4u,16u};
                    g_balance.selected_cell=highest+1u;
                    g_balance.requested_mask=masks[highest];
                    g_balance.command_result=BQ_BalanceSet(masks[highest]);
                    ++g_balance.command_count;
                    g_balance.result=g_balance.command_result;
                    if (!g_balance.command_result) {
                        g_balance.running=1; g_balance.activation_seen=0;
                        g_balance.elapsed_s=0;
                        g_balance.started_tick=HAL_GetTick();
                        balance_last_command=g_balance.started_tick;
                        g_balance.remaining_ms=g_balance.duration_ms;
                        balance_last_poll=g_balance.started_tick-250u;
                        balance_monitor_until=g_balance.started_tick+2000u;
                    }
                }
            }
        }
    }
    now=HAL_GetTick();
    if (!g_balance.running && g_balance.active_mask &&
        now-balance_last_command>=1500u) BalanceStop(0);
    if ((g_balance.running || g_balance.stop_pending || g_balance.active_mask ||
        (int32_t)(balance_monitor_until-now)>0) && now-balance_last_poll>=250u) {
        uint16_t mask=0, seconds=0;
        balance_last_poll=now;
        g_balance.status_result=BQ_BalanceStatus(&mask,&seconds);
        if (!g_balance.status_result) {
            g_balance.active_mask=mask;
            /* Retain the observed BMIC time after stopping for the demo. */
            if (g_balance.running && seconds>g_balance.elapsed_s) g_balance.elapsed_s=seconds;
            g_balance.status_tick=HAL_GetTick();
            if (mask && mask==g_balance.requested_mask) g_balance.activation_seen=1;
            if (g_balance.running && !g_balance.activation_seen &&
                HAL_GetTick()-g_balance.started_tick>1800u) BalanceStop(-26);
        } else if (g_balance.running) BalanceStop(g_balance.status_result);
    }
}
static int ChargeAllowed(void)
{
    if (!g_bms.setup_ok || !g_bms.data_valid || g_bms.sample_result ||
        HAL_GetTick()-g_bms.sample_tick>500u) return -20;
    if (g_control.ai_alarm || ProtectionFault()) return -21;
    if (!g_charge.chgpg || !g_bms.estop_ok || !(g_bms.power_gpio_mask&1u) ||
        !(g_bms.sensor.fet_status&1u) || g_bms.stage || (g_bms.power_gpio_mask&6u) ||
        g_balance.running || g_balance.stop_pending) return -22;
    if (g_bms.sensor.ntc_valid_mask!=15u || g_charge.state==4u) return -23;
    for (unsigned i=0;i<4u;++i) {
        float v=g_bms.sensor.cell_V[i],t=g_bms.sensor.temp_C[i];
        if (!isfinite(v) || v<3.0f || v>4.2f || !isfinite(t) || t<0.0f || t>45.0f)
            return -23;
    }
    return 0;
}
static void ChargeUpdate(int cancelled)
{
    /* These three pins remain INPUTS. Their LEDs are driven by BQ24610.
     * Never force STAT1/STAT2/PG to fake a charging indication. */
    g_charge.stat1_n=HAL_GPIO_ReadPin(CHG_STAT1_GPIO_Port,CHG_STAT1_Pin)==GPIO_PIN_SET;
    g_charge.stat2_n=HAL_GPIO_ReadPin(CHG_STAT2_GPIO_Port,CHG_STAT2_Pin)==GPIO_PIN_SET;
    g_charge.pg_n=HAL_GPIO_ReadPin(CHG_PG_GPIO_Port,CHG_PG_Pin)==GPIO_PIN_SET;
    g_charge.chgon=!g_charge.stat1_n; g_charge.chgfn=!g_charge.stat2_n;
    g_charge.chgpg=!g_charge.pg_n;
    g_charge.charging=g_charge.chgpg && g_charge.chgon && !g_charge.chgfn;
    g_charge.complete=g_charge.chgpg && !g_charge.chgon && g_charge.chgfn;
    g_charge.state=!g_charge.chgpg?0u:g_charge.charging?1u:g_charge.complete?2u:
        (g_charge.chgon && g_charge.chgfn)?4u:3u;
    g_charge.sample_tick=HAL_GetTick();
    g_charge.command_enabled=HAL_GPIO_ReadPin(CHG_CMD_GPIO_Port,CHG_CMD_Pin)==GPIO_PIN_SET;
    if (cancelled || g_charge.off) {
        g_charge.on=g_charge.off=0;
        HAL_GPIO_WritePin(CHG_CMD_GPIO_Port,CHG_CMD_Pin,GPIO_PIN_RESET);
        g_charge.command_enabled=0;
        if (cancelled) g_charge.result=-21;
    } else if (g_charge.on) {
        g_charge.on=0; g_charge.result=ChargeAllowed();
        if (!g_charge.result) {
            HAL_GPIO_WritePin(CHG_CMD_GPIO_Port,CHG_CMD_Pin,GPIO_PIN_SET);
            g_charge.command_enabled=1;
        }
    }
    if (g_charge.command_enabled) {
        g_charge.result=ChargeAllowed();
        if (g_charge.result) {
            HAL_GPIO_WritePin(CHG_CMD_GPIO_Port,CHG_CMD_Pin,GPIO_PIN_RESET);
            g_charge.command_enabled=0;
        }
    }
}
static void ClearTriggers(void)
{
    if (remote_motor_start) g_bms.remote_motor_result = -16;
    remote_motor_start = 0;
    g_control.step7_2 = g_control.step7_3 = g_control.all_on = 0;
    g_control.off = g_control.configure = 0;
    g_control.motor_on = g_control.motor_off = 0;
}
static void LoadsOff(void)
{
    Board_ForceSafeOutputs();
    g_bms.stage = 0;
}
static void Configure(void)
{
    uint16_t id = 0;
    int id_result;
    ClearTriggers();
    LoadsOff();
    g_bms.data_valid = 0;
    g_bms.configure_result = BQ_Configure();
    if (!g_bms.configure_result) HAL_Delay(1200u); /* First NORMAL conversions. */
    id_result = BQ_ReadID(&id);
    g_bms.device_id = id;
    if (!g_bms.configure_result) g_bms.configure_result = id_result;
    g_bms.setup_ok = g_bms.configure_result == 0;
    /* Establish the host OFF latch AFTER CFGUPDATE. */
    g_bms.fet_off_result = BQ_DisableFETs();
    last_sample = HAL_GetTick() - 100u;
}
void BMS_App_Init(I2C_HandleTypeDef *bus)
{
    ai_stop_applied = remote_motor_start = 0u;
    g_balance=(BMS_Balance){.enable=1u,.status_result=1};
    g_charge=(BMS_Charge){0};
    balance_last_command=balance_last_poll=balance_monitor_until=balance_last_attempt=0u;
    g_control = (BMS_Control){.motor1_permille = 900, .motor2_permille = 900};
    g_bms = (BMS_Status){0};
    BQ_Init(bus);
    Configure();
}
/* Remote commands never bypass BMIC protection or the physical ESTOP gate.
 * Existing Live Expressions triggers keep their original meanings. */
static int RemoteMotorAllowed(void)
{
    if (g_control.ai_alarm || g_control.off || g_control.configure || g_control.motor_off)
        return -10;
    if (!g_bms.setup_ok || !g_bms.data_valid || g_bms.sample_result ||
        HAL_GetTick()-g_bms.sample_tick > 1000u) return -11;
    if (HAL_GPIO_ReadPin(ESTOP_OK_GPIO_Port, ESTOP_OK_Pin) != GPIO_PIN_SET) return -12;
    if (ProtectionFault()) return -13;
    return 0;
}
int BMS_RemoteMotorOn(void)
{
    int rc = RemoteMotorAllowed();
    if (rc) { g_bms.remote_motor_result = rc; return rc; }
    g_control.motor1_permille = g_control.motor2_permille = 900;
    g_control.motor_on = 1u;
    remote_motor_start = 1u;
    g_bms.remote_motor_result = 1;
    return 0; /* Queued, not proof of PWM/rotation. */
}
void BMS_App_Update(void)
{
    BQ_Data sample;
    uint32_t now;
    int cancel_balance=g_control.off || g_control.ai_alarm || g_control.configure ||
        g_control.step7_2 || g_control.step7_3 || g_control.motor_on || g_control.all_on ||
        g_charge.on || g_charge.command_enabled;
    int cancel_charge=g_control.off || g_control.ai_alarm || g_control.configure ||
        g_control.motor_on || g_control.all_on;
    ++g_bms.heartbeat;
    /* AI alarm holds the existing full OFF path and cancels pending ONs.
     * One BMIC OFF attempt per AI event; explicit OFF can request another. */
    if (!g_control.ai_alarm) ai_stop_applied = 0u;
    if (g_control.off || g_control.ai_alarm) {
        int send_off = g_control.off || !ai_stop_applied;
        ClearTriggers();
        LoadsOff();
        if (send_off) {
            g_bms.last_action = g_control.ai_alarm ? 200u : 0u;
            ++g_bms.action_count;
            g_bms.fet_off_result = BQ_DisableFETs();
        }
        ai_stop_applied = g_control.ai_alarm != 0u;
    } else if (g_control.configure) {
        g_bms.last_action = 3;
        ++g_bms.action_count;
        Configure();
    } else {
        if (remote_motor_start) {
            remote_motor_start = 0;
            g_control.motor_on = 0;
            int rc = RemoteMotorAllowed();
            /* Cancel charge and balancing before enabling any load power. */
            g_balance.start = g_balance.enable = 0;
            if (g_balance.running || g_balance.active_mask || g_balance.stop_pending) {
                BalanceStop(-21);
                if (g_balance.command_result) rc = -14;
            }
            ChargeUpdate(1);
            if (!rc) {
                g_bms.fet_on_result = BQ_EnableFETs();
                if (g_bms.fet_on_result) rc = -15;
            }
            g_bms.remote_motor_result = rc;
            if (!rc) {
                HAL_GPIO_WritePin(BMS_ALLOW_GPIO_Port, BMS_ALLOW_Pin, GPIO_PIN_SET);
                g_control.motor_on = 1u;
            } else {
                LoadsOff();
                g_control.off = 1u; /* Cleanup any partially accepted FET command. */
            }
        }
        if (g_control.step7_2 && !g_control.off) {
            g_control.step7_2 = 0;
            g_bms.last_action = 72;
            ++g_bms.action_count;
            g_bms.fet_on_result = BQ_EnableFETs();
        }
        if (g_control.step7_3 && !g_control.off) {
            g_control.step7_3 = 0;
            g_bms.last_action = 73;
            ++g_bms.action_count;
            HAL_GPIO_WritePin(BMS_ALLOW_GPIO_Port, BMS_ALLOW_Pin, GPIO_PIN_SET);
        }
        if (g_control.motor_off && !g_control.off) {
            g_control.motor_off = g_control.motor_on = g_control.all_on = 0;
            Board_MotorSet(0, 0);
            HAL_GPIO_WritePin(MOTOR_CMD_GPIO_Port, MOTOR_CMD_Pin, GPIO_PIN_RESET);
            HAL_GPIO_WritePin(DCDC_CMD_GPIO_Port, DCDC_CMD_Pin, GPIO_PIN_RESET);
            g_bms.stage = 0;
            g_bms.last_action = 101;
            ++g_bms.action_count;
        } else if ((g_control.all_on || g_control.motor_on) && !g_control.off) {
            g_control.all_on = g_control.motor_on = 0;
            g_bms.last_action = 100;
            ++g_bms.action_count;
            /* No FET command or BMS_ALLOW write: complete step 7 manually. */
            HAL_GPIO_WritePin(MOTOR_CMD_GPIO_Port, MOTOR_CMD_Pin, GPIO_PIN_RESET);
            Board_MotorSet(0, 0);
            HAL_GPIO_WritePin(DCDC_CMD_GPIO_Port, DCDC_CMD_Pin, GPIO_PIN_SET);
            phase_tick = HAL_GetTick();
            g_bms.stage = 1;
        }
    }
    now = HAL_GetTick();
    if (!g_control.off) {
        if (g_bms.stage == 1 && now - phase_tick >= 100u) {
            HAL_GPIO_WritePin(MOTOR_CMD_GPIO_Port, MOTOR_CMD_Pin, GPIO_PIN_SET);
            phase_tick = now;
            g_bms.stage = 2;
        } else if (g_bms.stage == 2 && now - phase_tick >= 2u) {
            g_bms.stage = 3;
        }
        if (g_bms.stage == 3)
            Board_MotorSet(Clamp(g_control.motor1_permille), Clamp(g_control.motor2_permille));
    }
    /* Continuous sensing. Communication errors update status only. */
    if (now - last_sample >= 100u) {
        last_sample = now;
        g_bms.data_valid = 0;
        g_bms.sample_result = BQ_Read(&sample);
        if (!g_bms.sample_result) {
            g_bms.sensor = sample;
            g_bms.sample_tick = HAL_GetTick();
            ++g_bms.sample_count;
            g_bms.data_valid = 1;
        } else ++g_bms.error_count;
    }
    /* Status and LEDs only: no automatic changes to GPIO/PWM power commands. */
    g_bms.estop_ok = HAL_GPIO_ReadPin(ESTOP_OK_GPIO_Port, ESTOP_OK_Pin) == GPIO_PIN_SET;
    g_bms.converter_pg = HAL_GPIO_ReadPin(CONVERTER_PG_GPIO_Port, CONVERTER_PG_Pin) == GPIO_PIN_SET;
    g_bms.motor_fault_mask =
        (HAL_GPIO_ReadPin(M1_FAULTN_GPIO_Port, M1_FAULTN_Pin) != GPIO_PIN_SET ? 1u : 0u) |
        (HAL_GPIO_ReadPin(M2_FAULTN_GPIO_Port, M2_FAULTN_Pin) != GPIO_PIN_SET ? 2u : 0u);
    g_bms.power_gpio_mask =
        (HAL_GPIO_ReadPin(BMS_ALLOW_GPIO_Port, BMS_ALLOW_Pin) == GPIO_PIN_SET ? 1u : 0u) |
        (HAL_GPIO_ReadPin(DCDC_CMD_GPIO_Port, DCDC_CMD_Pin) == GPIO_PIN_SET ? 2u : 0u) |
        (HAL_GPIO_ReadPin(MOTOR_CMD_GPIO_Port, MOTOR_CMD_Pin) == GPIO_PIN_SET ? 4u : 0u);
    ChargeUpdate(cancel_charge);
    BalanceUpdate(cancel_balance);
    HAL_GPIO_WritePin(STATE_LED_GPIO_Port, STATE_LED_Pin,
                     g_bms.power_gpio_mask ? GPIO_PIN_RESET : GPIO_PIN_SET);
    HAL_GPIO_WritePin(FAULT_LED_GPIO_Port, FAULT_LED_Pin,
                     (ProtectionFault() || g_bms.sample_result || g_bms.configure_result || g_bms.fet_on_result ||
                      g_bms.fet_off_result) ? GPIO_PIN_RESET : GPIO_PIN_SET);
    HAL_GPIO_WritePin(AI_LED_GPIO_Port, AI_LED_Pin,
                     g_control.ai_alarm ? GPIO_PIN_RESET : GPIO_PIN_SET);
}
