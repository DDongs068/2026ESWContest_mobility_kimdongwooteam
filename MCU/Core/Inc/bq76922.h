#ifndef BQ76922_H
#define BQ76922_H

#include "stm32g4xx_hal.h"

typedef struct {
    float cell_V[4];
    float stack_V, pack_V, ld_V;
    float current_A;
    float temp_C[4];
    uint16_t battery_status, mfg_status;
    uint8_t fet_status, safety_status[3], pf_status[4];
    uint8_t ntc_valid_mask;
} BQ_Data;

extern volatile uint32_t bq_i2c_ok, bq_i2c_errors, bq_last_hal_error;
extern volatile int bq_last_error;

void BQ_Init(I2C_HandleTypeDef *i2c);
int BQ_ReadID(uint16_t *device_id);

int BQ_Configure(void);

int BQ_Read(BQ_Data *data);

int BQ_EnableFETs(void);

int BQ_PrepareFETs(void);
int BQ_DisableFETs(void);

int BQ_BalanceSet(uint16_t mask);
int BQ_BalanceStatus(uint16_t *mask, uint16_t *seconds);
int BQ_BalanceConfig(uint8_t *config, uint8_t *interval_s);

#endif
