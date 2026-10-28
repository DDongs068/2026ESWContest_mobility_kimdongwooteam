#include "main.h"
#include "stm32g4xx_it.h"

/* Exception vectors are required; none requests an MCU reset. */
void NMI_Handler(void)        { Platform_FatalFault(2); }
void HardFault_Handler(void)  { Platform_FatalFault(3); }
void MemManage_Handler(void)  { Platform_FatalFault(4); }
void BusFault_Handler(void)   { Platform_FatalFault(5); }
void UsageFault_Handler(void) { Platform_FatalFault(6); }
void SVC_Handler(void)        { }
void DebugMon_Handler(void)   { }
void PendSV_Handler(void)     { }
void SysTick_Handler(void)    { HAL_IncTick(); }
