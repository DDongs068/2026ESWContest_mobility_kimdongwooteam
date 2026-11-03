#include "bms_app.h"
#include "main.h"
#include "bms_demo.h"
#include <math.h>

volatile BMS_Control g_control;
volatile BMS_Status g_bms;
volatile BMS_Balance g_balance;
volatile BMS_Charge g_charge;
static uint32_t last_sample, motor_tick, fet_tick, fet_sample_count;
static uint32_t balance_command_tick, balance_poll_tick, balance_attempt_tick;
static int off_pending;

static int ProtectionFault(void)
{
    return (g_bms.sensor.safety_status[0]&0xFCu) ||
           (g_bms.sensor.safety_status[1]&0xF7u) ||
           (g_bms.sensor.safety_status[2]&0xF6u) ||
           (g_bms.sensor.pf_status[0]&0xDFu) ||
           (g_bms.sensor.pf_status[1]&0x9Fu) || g_bms.sensor.pf_status[2] ||
           (g_bms.sensor.pf_status[3]&1u);
}
static int SensingAllowed(void)
{
    if (!g_demo.started) return -40;
    if (!g_bms.setup_ok || !g_bms.data_valid || g_bms.sample_result ||
        HAL_GetTick()-g_bms.sample_tick>500u) return -11;
    if (g_bms.ai_alarm) return -17;
    if (!g_bms.estop_ok) return -12;
    if (g_demo.active && g_demo.fault) return -44;
    return ProtectionFault() ? -13 : 0;
}
static void ReadInputs(void)
{
    g_bms.estop_ok=HAL_GPIO_ReadPin(ESTOP_OK_GPIO_Port,ESTOP_OK_Pin)==GPIO_PIN_SET;
    g_bms.converter_pg=HAL_GPIO_ReadPin(CONVERTER_PG_GPIO_Port,CONVERTER_PG_Pin)==GPIO_PIN_SET;
    g_bms.motor_fault_mask=(HAL_GPIO_ReadPin(M1_FAULTN_GPIO_Port,M1_FAULTN_Pin)!=GPIO_PIN_SET?1u:0u) |
        (HAL_GPIO_ReadPin(M2_FAULTN_GPIO_Port,M2_FAULTN_Pin)!=GPIO_PIN_SET?2u:0u);
    g_bms.power_gpio_mask=(HAL_GPIO_ReadPin(BMS_ALLOW_GPIO_Port,BMS_ALLOW_Pin)==GPIO_PIN_SET?1u:0u) |
        (HAL_GPIO_ReadPin(DCDC_CMD_GPIO_Port,DCDC_CMD_Pin)==GPIO_PIN_SET?2u:0u) |
        (HAL_GPIO_ReadPin(MOTOR_CMD_GPIO_Port,MOTOR_CMD_Pin)==GPIO_PIN_SET?4u:0u);
    g_charge.stat1_n=HAL_GPIO_ReadPin(CHG_STAT1_GPIO_Port,CHG_STAT1_Pin)==GPIO_PIN_SET;
    g_charge.stat2_n=HAL_GPIO_ReadPin(CHG_STAT2_GPIO_Port,CHG_STAT2_Pin)==GPIO_PIN_SET;
    g_charge.pg_n=HAL_GPIO_ReadPin(CHG_PG_GPIO_Port,CHG_PG_Pin)==GPIO_PIN_SET;
    g_charge.chgon=!g_charge.stat1_n; g_charge.chgfn=!g_charge.stat2_n;
    g_charge.chgpg=!g_charge.pg_n;
    g_charge.charging=g_charge.chgpg && g_charge.chgon && !g_charge.chgfn;
    g_charge.complete=g_charge.chgpg && !g_charge.chgon && g_charge.chgfn;
    g_charge.state=!g_charge.chgpg?0u:g_charge.charging?1u:g_charge.complete?2u:
        (g_charge.chgon && g_charge.chgfn)?4u:3u;
    g_charge.command_enabled=HAL_GPIO_ReadPin(CHG_CMD_GPIO_Port,CHG_CMD_Pin)==GPIO_PIN_SET;
    g_charge.sample_tick=HAL_GetTick();
}
static void Sample(void)
{
    uint32_t now=HAL_GetTick();
    if (now-last_sample<BMS_SAMPLE_PERIOD_MS) return;
    last_sample=now;
    BQ_Data sample;
    g_bms.data_valid=0;
    g_bms.sample_result=BQ_Read(&sample);
    ++g_bms.sample_attempt_count;
    if (!g_bms.sample_result) {
        g_bms.sensor=sample; g_bms.sample_tick=HAL_GetTick();
        ++g_bms.sample_count; g_bms.data_valid=1;
    } else ++g_bms.error_count;
}
static void MotorOff(void)
{
    if (g_bms.stage || g_motor.pwm1_permille || g_motor.pwm2_permille)
        Board_MotorSet(0,0);
    HAL_GPIO_WritePin(MOTOR_CMD_GPIO_Port,MOTOR_CMD_Pin,GPIO_PIN_RESET);
    HAL_GPIO_WritePin(DCDC_CMD_GPIO_Port,DCDC_CMD_Pin,GPIO_PIN_RESET);
    g_bms.stage=0;
}
static void ChargeOff(void)
{
    HAL_GPIO_WritePin(CHG_CMD_GPIO_Port,CHG_CMD_Pin,GPIO_PIN_RESET);
    g_charge.command_enabled=0;
}
void BMS_StopAllOutputs(void)
{
    int was_active=g_control.fet_on || g_control.motor_on || g_control.charge_on ||
        g_bms.fet_state || g_bms.stage || g_charge.command_enabled;
    g_control=(BMS_Control){0};

    Board_ForceSafeOutputs();
    g_bms.stage=g_bms.fet_state=g_bms.fet_enabled=0;
    g_charge.command_enabled=0;
    ReadInputs();
    if (was_active) off_pending=1;
}
static void BalanceStop(int reason)
{
    g_balance.running=g_balance.requested_mask=0;
    g_balance.command_result=BQ_BalanceSet(0);
    ++g_balance.command_count;
    balance_command_tick=HAL_GetTick();

    g_balance.stop_pending=1;
    balance_poll_tick=balance_command_tick-250u;
    g_balance.result=g_balance.command_result?g_balance.command_result:reason;
}
static int BalanceAllowed(void)
{
    int rc=SensingAllowed();
    if (rc) return rc;
    if (g_control.motor_on || g_bms.stage || g_bms.fet_state==1u ||
        g_bms.fet_state==2u) return -22;
    if (g_bms.sensor.ntc_valid_mask!=15u || !isfinite(g_bms.sensor.current_A)) return -23;

    if (g_charge.command_enabled) {
        if (g_bms.sensor.current_A < -0.1f) return -23;
    } else if (fabsf(g_bms.sensor.current_A)>0.1f) return -23;
    for (unsigned i=0;i<4u;++i) {
        float v=g_bms.sensor.cell_V[i],t=g_bms.sensor.temp_C[i];
        if (!isfinite(v)||v<2.7f||v>4.2f||!isfinite(t)||t<0.0f||t>45.0f) return -23;
    }
    return 0;
}
static void BalanceUpdate(void)
{
    uint32_t now=HAL_GetTick();

    if (now-balance_poll_tick>=250u) {
        uint16_t mask=0,seconds=0;
        balance_poll_tick=now;
        g_balance.status_result=BQ_BalanceStatus(&mask,&seconds);
        if (!g_balance.status_result) {
            g_balance.active_mask=mask; g_balance.status_tick=HAL_GetTick();
            g_balance.elapsed_s=seconds;
            if (!mask && !g_balance.running && !g_balance.command_result) g_balance.stop_pending=0;
            if (g_balance.running && mask==g_balance.requested_mask) g_balance.activation_seen=1;
        }
    }
    int allowed=BalanceAllowed();
    if (allowed || g_balance.status_result) {
        if (g_balance.running || (g_balance.active_mask && !g_balance.stop_pending))
            BalanceStop(allowed?allowed:g_balance.status_result);
        if (g_balance.stop_pending && HAL_GetTick()-balance_command_tick>=500u)
            BalanceStop(allowed?allowed:-21);
        if (!g_balance.running) g_balance.result=allowed?allowed:g_balance.status_result;
        return;
    }
    if (g_balance.stop_pending) {
        if (now-balance_command_tick>=500u) BalanceStop(0);
        return;
    }
    float lowest=g_bms.sensor.cell_V[0]; unsigned highest=0;
    for (unsigned i=1;i<4u;++i) {
        if (g_bms.sensor.cell_V[i]<lowest) lowest=g_bms.sensor.cell_V[i];
        if (g_bms.sensor.cell_V[i]>g_bms.sensor.cell_V[highest]) highest=i;
    }
    if (g_balance.running) {
        if (g_bms.sensor.cell_V[g_balance.selected_cell-1u]-lowest<=0.010f) { BalanceStop(0); return; }
        if (!g_balance.activation_seen && now-g_balance.started_tick>1800u) { BalanceStop(-26); return; }
        uint32_t refresh=g_balance.interval_s*500u;
        if (refresh>5000u) refresh=5000u;
        if (now-balance_command_tick>=refresh) {
            g_balance.command_result=BQ_BalanceSet((uint16_t)g_balance.requested_mask);
            ++g_balance.command_count; balance_command_tick=HAL_GetTick();
            if (g_balance.command_result) BalanceStop(g_balance.command_result);
        }
        return;
    }
    if (g_balance.active_mask) { BalanceStop(-24); return; }
    if (now-balance_attempt_tick<1000u) return;
    balance_attempt_tick=now;
    if (g_bms.sensor.cell_V[highest]-lowest<0.020f) {g_balance.result=-25;return;}
    uint8_t config=0,interval=0;
    g_balance.command_result=BQ_BalanceConfig(&config,&interval);
    g_balance.config=config;g_balance.interval_s=interval;
    if (g_balance.command_result) {g_balance.result=g_balance.command_result;return;}
    if ((config&0x13u)||!interval) {g_balance.result=-24;return;}
    const uint16_t masks[4]={1,2,4,16};
    g_balance.selected_cell=highest+1u;g_balance.requested_mask=masks[highest];
    g_balance.command_result=BQ_BalanceSet(masks[highest]);++g_balance.command_count;
    g_balance.result=g_balance.command_result;
    if (!g_balance.command_result) {
        g_balance.running=1;g_balance.activation_seen=0;
        g_balance.started_tick=balance_command_tick=HAL_GetTick();
    } else g_balance.requested_mask=0;
}
static int ChargeAllowed(void)
{
    int rc=SensingAllowed(); if (rc) return rc;
    if (!g_bms.fet_enabled || !g_charge.chgpg || !(g_bms.sensor.fet_status&1u) ||
        g_bms.stage || g_control.motor_on || g_balance.stop_pending || g_balance.status_result)
        return -22;
    if (g_bms.sensor.ntc_valid_mask!=15u || g_charge.state==4u) return -23;
    for (unsigned i=0;i<4u;++i) {
        float v=g_bms.sensor.cell_V[i],t=g_bms.sensor.temp_C[i];
        if (!isfinite(v)||v<3.0f||v>4.2f||!isfinite(t)||t<0.0f||t>45.0f) return -23;
    }
    return 0;
}
static void ChargeUpdate(void)
{
    if (!g_control.charge_on) {ChargeOff();return;}
    if (g_control.fet_on && !g_bms.fet_enabled) {ChargeOff();return;}
    g_charge.result=ChargeAllowed();
    if (g_charge.result) {g_control.charge_on=0;ChargeOff();return;}
    HAL_GPIO_WritePin(CHG_CMD_GPIO_Port,CHG_CMD_Pin,GPIO_PIN_SET);
    g_charge.command_enabled=HAL_GPIO_ReadPin(CHG_CMD_GPIO_Port,CHG_CMD_Pin)==GPIO_PIN_SET;
}
static void RejectFET(int reason)
{
    g_bms.fet_on_result=reason;
    BMS_StopAllOutputs();
}
static void FETUpdate(void)
{
    if (!g_control.fet_on) {
        int was_on=g_bms.fet_state || g_bms.fet_enabled;
        if (g_control.motor_on) g_bms.motor_result=-18;
        if (g_control.charge_on) g_charge.result=-22;
        g_control.motor_on=g_control.charge_on=0;
        MotorOff();ChargeOff();
        HAL_GPIO_WritePin(BMS_ALLOW_GPIO_Port,BMS_ALLOW_Pin,GPIO_PIN_RESET);
        g_bms.fet_state=g_bms.fet_enabled=0;
        if (was_on) off_pending=1;
        return;
    }
    int rc=SensingAllowed();
    if (rc) {RejectFET(rc);return;}
    if (!g_bms.fet_state) {
        MotorOff();ChargeOff();
        HAL_GPIO_WritePin(BMS_ALLOW_GPIO_Port,BMS_ALLOW_Pin,GPIO_PIN_RESET);
        g_bms.fet_state=1;fet_tick=HAL_GetTick();
        BalanceStop(0);
    }
    if (g_bms.fet_state==1u) {
        if (g_balance.command_result || g_balance.status_result) {RejectFET(-14);return;}
        if (g_balance.stop_pending || g_balance.active_mask) {
            if (HAL_GetTick()-fet_tick>2000u) RejectFET(-14);
            return;
        }
        g_bms.fet_on_result=BQ_PrepareFETs();
        if (g_bms.fet_on_result) {RejectFET(g_bms.fet_on_result);return;}
        HAL_GPIO_WritePin(BMS_ALLOW_GPIO_Port,BMS_ALLOW_Pin,GPIO_PIN_SET);
        g_bms.fet_state=2;fet_tick=HAL_GetTick();fet_sample_count=g_bms.sample_count;
        ++g_bms.action_count;g_bms.last_action=1;
        return;
    }
    if (g_bms.fet_state==2u) {
        if (g_bms.sample_count!=fet_sample_count &&
            (g_bms.sensor.fet_status&5u)==5u) {
            g_bms.fet_enabled=1;g_bms.fet_state=3;g_bms.fet_on_result=0;
        } else if (HAL_GetTick()-fet_tick>1000u) RejectFET(-19);
        return;
    }
    if ((g_bms.sensor.fet_status&5u)!=5u) RejectFET(-19);
}
static void MotorUpdate(void)
{
    if (!g_control.motor_on) {MotorOff();return;}
    if (!g_bms.fet_enabled) {MotorOff();return;}
    int rc=SensingAllowed();
    if (!rc && ((BMS_MOTOR1_PERMILLE && (g_bms.motor_fault_mask&1u)) ||
                (BMS_MOTOR2_PERMILLE && (g_bms.motor_fault_mask&2u)))) rc=-27;
    if (!rc && (g_balance.command_result || g_balance.status_result)) rc=-14;
    if (rc) {g_bms.motor_result=rc;g_control.motor_on=0;MotorOff();return;}
    if (g_balance.running || g_balance.active_mask || g_balance.stop_pending) {MotorOff();return;}
    if (!g_bms.stage) {
        ChargeOff();Board_MotorSet(0,0);
        HAL_GPIO_WritePin(DCDC_CMD_GPIO_Port,DCDC_CMD_Pin,
                         BMS_MOTOR_DIRECT_DC_LINK?GPIO_PIN_RESET:GPIO_PIN_SET);
        motor_tick=HAL_GetTick();g_bms.stage=1;g_bms.motor_result=0;
    } else if (g_bms.stage==1u && HAL_GetTick()-motor_tick>=100u) {
        HAL_GPIO_WritePin(MOTOR_CMD_GPIO_Port,MOTOR_CMD_Pin,GPIO_PIN_SET);
        motor_tick=HAL_GetTick();g_bms.stage=2;
    } else if (g_bms.stage==2u && HAL_GetTick()-motor_tick>=2u) g_bms.stage=3;
    if (g_bms.stage==3u) Board_MotorSet(BMS_MOTOR1_PERMILLE,BMS_MOTOR2_PERMILLE);
}
static void Indicators(void)
{
    int fault=(g_demo.active && g_demo.fault) || ProtectionFault() || g_bms.sample_result ||
        g_bms.configure_result || g_bms.fet_on_result || g_bms.fet_off_result;
    HAL_GPIO_WritePin(STATE_LED_GPIO_Port,STATE_LED_Pin,
                     g_bms.power_gpio_mask?GPIO_PIN_RESET:GPIO_PIN_SET);
    HAL_GPIO_WritePin(FAULT_LED_GPIO_Port,FAULT_LED_Pin,fault?GPIO_PIN_RESET:GPIO_PIN_SET);
    HAL_GPIO_WritePin(AI_LED_GPIO_Port,AI_LED_Pin,g_bms.ai_alarm?GPIO_PIN_RESET:GPIO_PIN_SET);
}
void BMS_App_Init(I2C_HandleTypeDef *bus)
{
    BMS_DemoInit();
    g_control=(BMS_Control){0};g_bms=(BMS_Status){0};
    g_charge=(BMS_Charge){0};g_balance=(BMS_Balance){.status_result=1};
    off_pending=0;motor_tick=fet_tick=0;
    balance_command_tick=balance_poll_tick=balance_attempt_tick=HAL_GetTick();
    Board_ForceSafeOutputs();BQ_Init(bus);
    g_bms.configure_result=BQ_Configure();
    if (!g_bms.configure_result) HAL_Delay(1200u);
    uint16_t id=0;int rc=BQ_ReadID(&id);g_bms.device_id=id;
    if (!g_bms.configure_result) g_bms.configure_result=rc;
    g_bms.setup_ok=g_bms.configure_result==0;
    g_bms.fet_off_result=BQ_DisableFETs();
    g_balance.command_result=BQ_BalanceSet(0);++g_balance.command_count;
    uint16_t mask=0,seconds=0;
    g_balance.status_result=BQ_BalanceStatus(&mask,&seconds);
    if (!g_balance.status_result) {g_balance.active_mask=mask;g_balance.elapsed_s=seconds;}
    g_balance.stop_pending=g_balance.command_result!=0 || mask!=0 || g_balance.status_result!=0;
    last_sample=HAL_GetTick()-BMS_SAMPLE_PERIOD_MS;
}
int BMS_SetStarted(int started)
{
    if (started!=0 && started!=1) return -7;
    if (!started) {
        BMS_StopAllOutputs();
        g_bms.data_valid=0;
    } else if (!g_demo.started) {
        g_bms.session_sample_start=g_bms.sample_attempt_count;
        g_bms.data_valid=0;
        last_sample=HAL_GetTick()-BMS_SAMPLE_PERIOD_MS;
    }
    BMS_DemoSetRunning(started);
    return 0;
}
int BMS_RequestOutput(BMS_Output output,int on)
{
    if ((unsigned)output>BMS_OUTPUT_CHARGE || (on!=0 && on!=1)) return -7;
    if (!on) {
        if (output==BMS_OUTPUT_FET) {BMS_StopAllOutputs();off_pending=1;}
        else if (output==BMS_OUTPUT_MOTOR) g_control.motor_on=0;
        else g_control.charge_on=0;
        return 0;
    }
    int rc=SensingAllowed();if (rc) return rc;
    if (output==BMS_OUTPUT_FET) g_control.fet_on=1;
    else if (output==BMS_OUTPUT_MOTOR) {
        if (g_control.charge_on || g_charge.command_enabled) return -24;
        g_control.fet_on=g_control.motor_on=1;
    } else {
        if (g_control.motor_on || g_bms.stage) return -24;
        g_control.fet_on=g_control.charge_on=1;
    }
    return 0;
}
void BMS_App_Update(void)
{
    ++g_bms.heartbeat;
    if (g_demo.started) Sample();
    ReadInputs();
    int fault=SensingAllowed();
    if ((fault && (g_control.fet_on || g_control.motor_on || g_control.charge_on ||
                   g_bms.fet_state || g_charge.command_enabled || g_bms.stage)) ||
        g_bms.ai_alarm) {
        g_bms.motor_result=fault?fault:-17;
        if (g_control.charge_on || g_charge.command_enabled) g_charge.result=fault?fault:-17;
        BMS_StopAllOutputs();
    }
    if (g_control.fet_on>1u || g_control.motor_on>1u || g_control.charge_on>1u)
        RejectFET(-7);
    if (g_control.motor_on && g_control.charge_on) {
        g_control.motor_on=g_control.charge_on=0;
        g_bms.motor_result=g_charge.result=-24;MotorOff();ChargeOff();
    }
    if (off_pending) {

        g_bms.fet_off_result=BQ_DisableFETs();off_pending=0;
    }
    FETUpdate();

    BalanceUpdate();
    MotorUpdate();ChargeUpdate();ReadInputs();Indicators();
}
