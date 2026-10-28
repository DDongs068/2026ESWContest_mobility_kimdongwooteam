#ifndef BQ76922_H
#define BQ76922_H

#include "stm32g4xx_hal.h"

typedef struct {
    float cell_V[4];                 /* BMIC physical channels 1,2,3,5. */
    float stack_V, pack_V, ld_V;
    float current_A;                 /* Positive charge, negative discharge. */
    float temp_C[4];                 /* TS1, TS2, ALERT, CFETOFF. */
    uint16_t battery_status, mfg_status;
    uint8_t fet_status, safety_status[3], pf_status[4];
    uint8_t ntc_valid_mask;           /* Configured NTC pins only: not conversion
                                     * freshness or physical sensor validation. */
} BQ_Data;

extern volatile uint32_t bq_i2c_ok, bq_i2c_errors, bq_last_hal_error;
extern volatile int bq_last_error;

/* Main-loop calls only. Factory 7-bit address 0x08, no I2C CRC.
 * HAL transfers have a finite 25ms timeout. Zero means completed successfully;
 * negative values report transport/protocol/configuration errors. */
void BQ_Init(I2C_HandleTypeDef *i2c);
int BQ_ReadID(uint16_t *device_id);
/* Boot RAM setup with board power outputs OFF. Sets the four-cell map,
 * measurement units, nominal 10mOhm current scale, NTC pins and BOTHOFF input.
 * Existing protection thresholds/enables and valid NTC settings are preserved.
 * A failed setup can leave a partial RAM profile; status is reported to caller. */
int BQ_Configure(void);
/* Reads measurements and raw protection/mode status for observation.
 * No mode, voltage, temperature, safety or PF status blocks output control.
 * On error, the caller's previous BQ_Data is unchanged. */
int BQ_Read(BQ_Data *data);
/* FET permission is separate from the board BMS_ALLOW GPIO. These commands
 * preserve BMIC protections; actual CHG/DSG status is in fet_status bits0/2.
 * A successful command is not an independent measurement of FET conduction. */
int BQ_EnableFETs(void);
int BQ_DisableFETs(void);
/* SLUUCG7 Table13-32: read-only audit; NEVER enables/disables protection. */
typedef struct {
    uint8_t enabled[3], chg_map[3], dsg_map[3];
    uint8_t cuv_code, cov_code, scd_code, scd_delay_code;
} BQ_ProtectionProfile;
int BQ_ReadProtectionProfile(BQ_ProtectionProfile *profile);
/* Host balancing: physical channels 1,2,3,5; one cell per command, or0=stop.
 * Success means command accepted by I2C, not proof of active balancing. */
int BQ_BalanceSet(uint16_t mask);
int BQ_BalanceStatus(uint16_t *mask, uint16_t *seconds);
int BQ_BalanceConfig(uint8_t *config, uint8_t *interval_s);

#endif
