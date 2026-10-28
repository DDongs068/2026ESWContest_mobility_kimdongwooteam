#include "safety_supervisor.h"
#include "bms_app.h"
#include "main.h"
#include <math.h>
#include <string.h>
volatile Safety_Status g_safety;
static uint32_t dsg_wait_tick;
static int dsg_waiting;
static uint32_t evaluate(void)
{
    BMS_Status b=g_bms;
    const uint8_t sm[3]={0xFC,0xF7,0xF6},pm[4]={0xDF,0x9F,0xFF,0x01};
    uint32_t reasons=0,now=HAL_GetTick();
    /* Fail closed if critical protection or its FET action is disabled.
     * Required A: SCD/OCD1/OCC/COV/CUV; B: OTD/OTC/UTD/UTC.
     * No thresholds/enables/maps are automatically rewritten. */
    BQ_ProtectionProfile profile=g_safety.profile;
    if (!g_safety.profile_valid || g_safety.profile_result ||
        (profile.enabled[0]&0xBCu)!=0xBCu || (profile.enabled[1]&0x33u)!=0x33u ||
        (profile.chg_map[0]&0x18u)!=0x18u || (profile.chg_map[1]&0x11u)!=0x11u ||
        (profile.dsg_map[0]&0xA4u)!=0xA4u || (profile.dsg_map[1]&0x22u)!=0x22u ||
        profile.cuv_code<20 || profile.cuv_code>80 || profile.cov_code<20 || profile.cov_code>110 ||
        profile.scd_code>15 || profile.scd_delay_code<1 || profile.scd_delay_code>31)
        reasons|=SAFE_PROFILE;
    g_safety.sample_age_ms=now-b.sample_tick;
    g_safety.fet=b.sensor.fet_status;
    for (unsigned i=0;i<3;++i) {
        g_safety.safety[i]=(uint8_t)(b.sensor.safety_status[i]&sm[i]);
        if (g_safety.safety[i]) reasons|=SAFE_BMIC;
    }
    for (unsigned i=0;i<4;++i) {
        g_safety.pf[i]=(uint8_t)(b.sensor.pf_status[i]&pm[i]);
        if (g_safety.pf[i]) reasons|=SAFE_BMIC;
    }
    if (!b.setup_ok || b.configure_result) reasons|=SAFE_CONFIG;
    if (!b.data_valid || b.sample_result) reasons|=SAFE_COMM;
    if (!b.sample_count || now-b.sample_tick>500u) reasons|=SAFE_STALE;
    if (HAL_GPIO_ReadPin(ESTOP_OK_GPIO_Port,ESTOP_OK_Pin)!=GPIO_PIN_SET) reasons|=SAFE_EST0P;
    if (HAL_GPIO_ReadPin(M1_FAULTN_GPIO_Port,M1_FAULTN_Pin)!=GPIO_PIN_SET ||
        HAL_GPIO_ReadPin(M2_FAULTN_GPIO_Port,M2_FAULTN_Pin)!=GPIO_PIN_SET) reasons|=SAFE_DRIVER;
    /* Plausibility only. Real COV/CUV/OCD/OT setpoints remain the BMIC's RAM/OTP profile. */
    if (b.data_valid && !b.sample_result) {
        if (b.sensor.ntc_valid_mask!=15u || !isfinite(b.sensor.current_A) ||
            !isfinite(b.sensor.stack_V) || b.sensor.stack_V<0 || b.sensor.stack_V>24) reasons|=SAFE_SENSOR;
        for (unsigned i=0;i<4;++i)
            if (!isfinite(b.sensor.cell_V[i]) || b.sensor.cell_V[i]<0.5f || b.sensor.cell_V[i]>5.5f ||
                !isfinite(b.sensor.temp_C[i]) || b.sensor.temp_C[i]<-40 || b.sensor.temp_C[i]>125)
                reasons|=SAFE_SENSOR;
    }
    if (b.stage>=2u && (b.power_gpio_mask&1u) && !(b.sensor.fet_status&4u)) {
        if (!dsg_waiting) { dsg_waiting=1; dsg_wait_tick=now; }
        if (now-dsg_wait_tick>=300u) reasons|=SAFE_DSG;
    } else dsg_waiting=0;
    return reasons;
}
void Safety_Init(void)
{ g_safety=(Safety_Status){0}; dsg_waiting=0; }
void Safety_RefreshProfile(void)
{
    BQ_ProtectionProfile profile;
    int rc=BQ_ReadProtectionProfile(&profile);
    g_safety.profile_result=rc; g_safety.profile_valid=rc==0;
    if (!rc) g_safety.profile=profile;
    g_safety.profile_tick=HAL_GetTick();
}
int Safety_Rearm(void)
{
    g_safety.active=evaluate();
    if (g_safety.active || g_bms.stage || g_bms.power_gpio_mask || g_charge.command_enabled)
        return (g_safety.last_rearm_result=-50);
    g_safety.latched=0; g_safety.last_rearm_result=0; return 0;
}
void Safety_Update(void)
{
    if (HAL_GetTick()-g_safety.profile_tick>=5000u) Safety_RefreshProfile();
    g_safety.active=evaluate(); g_safety.last_tick=HAL_GetTick();
    if (g_safety.ack) { g_safety.ack=0; (void)Safety_Rearm(); }
    if (g_safety.active) g_balance.enable=0; /* No autonomous balancing on unknown protection/sensors. */
    int requested=g_control.step7_2 || g_control.step7_3 || g_control.motor_on ||
                  g_control.all_on || g_charge.on || g_balance.start;
    int powered=g_bms.stage || g_bms.power_gpio_mask || g_charge.command_enabled || g_balance.running;
    if ((g_safety.active && (requested || powered)) || (g_safety.latched && powered)) {
        if (!g_safety.latched) {
            ++g_safety.event_count; g_safety.first_trip_tick=g_safety.last_tick;
        }
        g_safety.latched|=g_safety.active;
        /* Drop physical GPIO/PWM immediately, before any subsequent I2C work. */
        Board_ForceSafeOutputs(); g_bms.stage=0; g_bms.power_gpio_mask=0;
        g_control.off=1;
    }
    if (g_safety.active || g_safety.latched)
        HAL_GPIO_WritePin(FAULT_LED_GPIO_Port,FAULT_LED_Pin,GPIO_PIN_RESET);
    if (g_safety.latched) {
        if (requested) g_control.off=1;
        g_control.step7_2=g_control.step7_3=g_control.motor_on=g_control.all_on=0;
        g_charge.on=0; g_balance.start=g_balance.enable=0;
        /* The BMS OFF path consumes off and makes one BMIC OFF attempt. No blind retry. */
    }
}
