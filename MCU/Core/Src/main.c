#include "main.h"
#include "bms_app.h"
#include "ethernet_test.h"
#include "demo_stream.h"
#include "safety_supervisor.h"

I2C_HandleTypeDef hi2c1;
volatile Motor_Status g_motor;
volatile uint32_t g_platform_reset_flags;
volatile uint32_t g_platform_fault_id;
static void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_I2C1_Init(void);

int main(void)
{

	g_platform_reset_flags = RCC->CSR;
    __HAL_RCC_CLEAR_RESET_FLAGS();
    if (HAL_Init() != HAL_OK) Error_Handler();
    MX_GPIO_Init();
    SystemClock_Config();
    MX_I2C1_Init();
    BMS_App_Init(&hi2c1);
    Ethernet_Test_Init();
    Safety_Init();
    Safety_RefreshProfile();
    while (1) {
        Safety_Update();
        Demo_Update();
        BMS_App_Update();
        Safety_Update();
        Ethernet_Test_Update();
        HAL_Delay(5u);
    }
}
static void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1) != HAL_OK)
  {
    Error_Handler();
  }

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK)
  {
    Error_Handler();
  }
}

static void MX_I2C1_Init(void)
{

  hi2c1.Instance = I2C1;
  
  hi2c1.Init.Timing = 0x10921E2F;
  hi2c1.Init.OwnAddress1 = 0;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2 = 0;
  hi2c1.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_I2CEx_ConfigAnalogFilter(&hi2c1, I2C_ANALOGFILTER_ENABLE) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_I2CEx_ConfigDigitalFilter(&hi2c1, 0) != HAL_OK)
  {
    Error_Handler();
  }
}

static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();

  HAL_GPIO_WritePin(GPIOA, PWM1_Pin | PWM2_Pin | CHG_CMD_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(W5500_SCSN_GPIO_Port, W5500_SCSN_Pin, GPIO_PIN_SET);
  HAL_GPIO_WritePin(GPIOB, BMS_ALLOW_Pin | MOTOR_CMD_Pin | DCDC_CMD_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOB, AI_LED_Pin | FAULT_LED_Pin, GPIO_PIN_SET);
  HAL_GPIO_WritePin(GPIOC, M1_DIR_Pin | M2_DIR_Pin | W5500_RSTN_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(STATE_LED_GPIO_Port, STATE_LED_Pin, GPIO_PIN_SET);

  GPIO_InitStruct.Pin = PWM1_Pin | PWM2_Pin | CHG_CMD_Pin | W5500_SCSN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = BMS_ALLOW_Pin | MOTOR_CMD_Pin | DCDC_CMD_Pin | AI_LED_Pin | FAULT_LED_Pin;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = M1_DIR_Pin | M2_DIR_Pin | W5500_RSTN_Pin | STATE_LED_Pin;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = CHG_STAT2_Pin | CHG_PG_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = M1_FAULTN_Pin | CHG_STAT1_Pin;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = M2_FAULTN_Pin | CONVERTER_PG_Pin | ESTOP_OK_Pin | W5500_INTN_Pin;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  /* PC8 is shorted to PC7 (M2_DIR); keep PC8 high-Z. */
  GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_12;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);
  GPIO_InitStruct.Pin = GPIO_PIN_2;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);
  
}

void Board_ForceSafeOutputs(void)
{

  HAL_GPIO_WritePin(GPIOB, BMS_ALLOW_Pin | MOTOR_CMD_Pin | DCDC_CMD_Pin, GPIO_PIN_RESET);
  if (RCC->APB1ENR1 & RCC_APB1ENR1_TIM2EN) {
    TIM2->CR1 = 0; TIM2->CCER = 0; TIM2->CCR1 = 0; TIM2->CCR2 = 0;
  }
  HAL_GPIO_WritePin(GPIOA, PWM1_Pin | PWM2_Pin | CHG_CMD_Pin, GPIO_PIN_RESET);
  GPIOA->MODER = (GPIOA->MODER & ~15u) | 5u;
  HAL_GPIO_WritePin(GPIOC, M1_DIR_Pin | M2_DIR_Pin, GPIO_PIN_RESET);
  g_motor.pwm1_permille = g_motor.pwm2_permille = 0;
  HAL_GPIO_WritePin(W5500_SCSN_GPIO_Port, W5500_SCSN_Pin, GPIO_PIN_SET);
}

void Board_MotorSet(int32_t m1, int32_t m2)
{
  static int32_t previous1, previous2;
  m1 = m1 > 1000 ? 1000 : m1 < -1000 ? -1000 : m1;
  m2 = m2 > 1000 ? 1000 : m2 < -1000 ? -1000 : m2;
  if (!(RCC->APB1ENR1 & RCC_APB1ENR1_TIM2EN)) {
    __HAL_RCC_TIM2_CLK_ENABLE();
    __HAL_RCC_TIM2_FORCE_RESET(); __HAL_RCC_TIM2_RELEASE_RESET();
  }
  if (!(TIM2->CR1 & TIM_CR1_CEN)) {
    TIM2->PSC=0; TIM2->ARR=799; TIM2->CCR1=0; TIM2->CCR2=0;
    TIM2->EGR=TIM_EGR_UG;
    TIM2->CR1=TIM_CR1_ARPE|TIM_CR1_CEN;
    previous1=0; previous2=0;
  }
  /* CEN alone does not prove that both outputs are configured. Reapply the
   * PA0/PA1 TIM2 AF1 and CH1/CH2 PWM settings even when the timer is running,
   * so a disabled CH2 or lost PA1 pin mux cannot silently discard motor2 duty.
   * Keep CH3/CH4 and all unrelated GPIO pins unchanged. */
  TIM2->CCMR1=(TIM2->CCMR1&~0xFFFFu)|(6u<<4)|(1u<<3)|(6u<<12)|(1u<<11);
  GPIOA->AFR[0]=(GPIOA->AFR[0]&~255u)|0x11u;
  GPIOA->OTYPER&=~3u;
  GPIOA->MODER=(GPIOA->MODER&~15u)|10u;
  TIM2->CCER=(TIM2->CCER&~0xFFu)|TIM_CCER_CC1E|TIM_CCER_CC2E;
  int change1=(m1<0 && previous1>0)||(m1>0 && previous1<0);
  int change2=(m2<0 && previous2>0)||(m2>0 && previous2<0);
  TIM2->CCR1=change1 ? 0u : (uint32_t)(m1<0 ? -m1 : m1)*800u/1000u;
  TIM2->CCR2=change2 ? 0u : (uint32_t)(m2<0 ? -m2 : m2)*800u/1000u;
  if (!change1) HAL_GPIO_WritePin(GPIOC,M1_DIR_Pin,m1<0?GPIO_PIN_RESET:GPIO_PIN_SET);
  if (!change2) HAL_GPIO_WritePin(GPIOC,M2_DIR_Pin,m2<0?GPIO_PIN_RESET:GPIO_PIN_SET);
  previous1=change1?0:m1; previous2=change2?0:m2;
  g_motor.pwm1_permille = previous1;
  g_motor.pwm2_permille = previous2;
  g_motor.pwm_hz = 20000u;
}

/* SPI1: PCB U3 pins19/20/21 = PA5 SCK / PA6 MISO / PA7 MOSI.
 * HSI16 / 8 = 2MHz, mode0, 8-bit. No IRQ/DMA or HAL SPI dependency. */
void Board_EthernetReset(void)
{
    GPIO_InitTypeDef gpio = {0};
    HAL_GPIO_WritePin(W5500_SCSN_GPIO_Port, W5500_SCSN_Pin, GPIO_PIN_SET);
    __HAL_RCC_SPI1_CLK_ENABLE();
    __HAL_RCC_SPI1_FORCE_RESET();
    __HAL_RCC_SPI1_RELEASE_RESET();
    gpio.Pin = GPIO_PIN_5 | GPIO_PIN_6 | GPIO_PIN_7;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    gpio.Alternate = GPIO_AF5_SPI1;
    HAL_GPIO_Init(GPIOA, &gpio);
    SPI1->CR2 = (7u << SPI_CR2_DS_Pos) | SPI_CR2_FRXTH;
    SPI1->CR1 = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI |
                (2u << SPI_CR1_BR_Pos) | SPI_CR1_SPE;
    HAL_GPIO_WritePin(W5500_RSTN_GPIO_Port, W5500_RSTN_Pin, GPIO_PIN_RESET);
    HAL_Delay(2u);
    HAL_GPIO_WritePin(W5500_RSTN_GPIO_Port, W5500_RSTN_Pin, GPIO_PIN_SET);
    HAL_Delay(60u);
}

int Board_EthernetTransfer(uint16_t address, uint8_t block, int write,
                           uint8_t *data, uint16_t length)
{
    uint8_t header[3] = {(uint8_t)(address >> 8), (uint8_t)address,
                         (uint8_t)((block << 3) | (write ? 4u : 0u))};
    uint32_t started = HAL_GetTick();
    if (!length || length > 2048u) return -1;
    HAL_GPIO_WritePin(W5500_SCSN_GPIO_Port, W5500_SCSN_Pin, GPIO_PIN_RESET);
    for (uint32_t i = 0; i < (uint32_t)length + 3u; ++i) {
        while (!(SPI1->SR & SPI_SR_TXE))
            if (HAL_GetTick() - started >= 25u) goto fail;
        *(__IO uint8_t *)&SPI1->DR = i < 3u ? header[i] : write ? data[i-3u] : 0u;
        while (!(SPI1->SR & SPI_SR_RXNE))
            if (HAL_GetTick() - started >= 25u) goto fail;
        uint8_t received = *(__IO uint8_t *)&SPI1->DR;
        if (i >= 3u && !write) data[i-3u] = received;
    }
    while (SPI1->SR & SPI_SR_BSY)
        if (HAL_GetTick() - started >= 25u) goto fail;
    HAL_GPIO_WritePin(W5500_SCSN_GPIO_Port, W5500_SCSN_Pin, GPIO_PIN_SET);
    return 0;
fail:
    HAL_GPIO_WritePin(W5500_SCSN_GPIO_Port, W5500_SCSN_Pin, GPIO_PIN_SET);
    SPI1->CR1 &= ~SPI_CR1_SPE; /* Next explicit/retry init resets SPI + W5500. */
    return -1;
}

/* CPU/setup faults stop for inspection; they never request a reset. */
__NO_RETURN void Platform_FatalFault(uint32_t fault_id)
{
    __disable_irq();
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOBEN | RCC_AHB2ENR_GPIOCEN;
    (void)RCC->AHB2ENR;
    Board_ForceSafeOutputs();
    g_platform_fault_id = fault_id;
    while (1) { __NOP(); }
}
void Error_Handler(void)
{
    Platform_FatalFault(1);
}
#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
    (void)file;
    (void)line;
    Platform_FatalFault(7);
}
#endif
