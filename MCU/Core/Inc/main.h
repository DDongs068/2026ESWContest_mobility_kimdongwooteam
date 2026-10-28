#ifndef MAIN_H
#define MAIN_H
#include "stm32g4xx_hal.h"
void Error_Handler(void);
void Board_ForceSafeOutputs(void);
void Board_MotorSet(int32_t m1, int32_t m2);
typedef struct {
    int32_t pwm1_permille, pwm2_permille; /* Actual timer duty with direction. */
    uint32_t pwm_hz;
} Motor_Status;
extern volatile Motor_Status g_motor;
void Board_EthernetReset(void);
int Board_EthernetTransfer(uint16_t address, uint8_t block, int write,
                           uint8_t *data, uint16_t length);
__NO_RETURN void Platform_FatalFault(uint32_t fault_id);
extern volatile uint32_t g_platform_reset_flags, g_platform_fault_id;
extern I2C_HandleTypeDef hi2c1;

#define PWM1_Pin GPIO_PIN_0
#define PWM1_GPIO_Port GPIOA
#define PWM2_Pin GPIO_PIN_1
#define PWM2_GPIO_Port GPIOA
#define CHG_CMD_Pin GPIO_PIN_2
#define CHG_CMD_GPIO_Port GPIOA
#define W5500_SCSN_Pin GPIO_PIN_4
#define W5500_SCSN_GPIO_Port GPIOA
#define CHG_STAT2_Pin GPIO_PIN_10
#define CHG_STAT2_GPIO_Port GPIOA
#define CHG_PG_Pin GPIO_PIN_15
#define CHG_PG_GPIO_Port GPIOA
#define AI_LED_Pin GPIO_PIN_0
#define AI_LED_GPIO_Port GPIOB
#define FAULT_LED_Pin GPIO_PIN_1
#define FAULT_LED_GPIO_Port GPIOB
#define M1_FAULTN_Pin GPIO_PIN_3
#define M1_FAULTN_GPIO_Port GPIOB
#define CHG_STAT1_Pin GPIO_PIN_7
#define CHG_STAT1_GPIO_Port GPIOB
#define BMS_SCL_Pin GPIO_PIN_8
#define BMS_SCL_GPIO_Port GPIOB
#define BMS_SDA_Pin GPIO_PIN_9
#define BMS_SDA_GPIO_Port GPIOB
#define BMS_ALLOW_Pin GPIO_PIN_11
#define BMS_ALLOW_GPIO_Port GPIOB
#define MOTOR_CMD_Pin GPIO_PIN_13
#define MOTOR_CMD_GPIO_Port GPIOB
#define DCDC_CMD_Pin GPIO_PIN_14
#define DCDC_CMD_GPIO_Port GPIOB
#define M2_FAULTN_Pin GPIO_PIN_2
#define M2_FAULTN_GPIO_Port GPIOC
#define CONVERTER_PG_Pin GPIO_PIN_4
#define CONVERTER_PG_GPIO_Port GPIOC
#define ESTOP_OK_Pin GPIO_PIN_5
#define ESTOP_OK_GPIO_Port GPIOC
#define M1_DIR_Pin GPIO_PIN_6
#define M1_DIR_GPIO_Port GPIOC
#define M2_DIR_Pin GPIO_PIN_7
#define M2_DIR_GPIO_Port GPIOC
#define W5500_RSTN_Pin GPIO_PIN_10
#define W5500_RSTN_GPIO_Port GPIOC
#define W5500_INTN_Pin GPIO_PIN_11
#define W5500_INTN_GPIO_Port GPIOC
#define STATE_LED_Pin GPIO_PIN_13
#define STATE_LED_GPIO_Port GPIOC

#endif
