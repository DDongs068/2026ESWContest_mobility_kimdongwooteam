CONFIG ?= Debug
TOOLCHAIN_BIN ?=
CROSS_COMPILE ?= arm-none-eabi-
HOST_CC ?= clang
BUILD_DIR := build/$(CONFIG)
OUTPUT_DIR := ../firmware/$(CONFIG)
TARGET := $(OUTPUT_DIR)/testest_proto
TOOLS := $(if $(strip $(TOOLCHAIN_BIN)),$(TOOLCHAIN_BIN)/,)$(CROSS_COMPILE)
CC := $(TOOLS)gcc
OBJCOPY := $(TOOLS)objcopy
SIZE := $(TOOLS)size
CPU := -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
INCLUDES := -ICore/Inc -IDrivers/STM32G4xx_HAL_Driver/Inc \
            -IDrivers/STM32G4xx_HAL_Driver/Inc/Legacy \
            -IDrivers/CMSIS/Device/ST/STM32G4xx/Include -IDrivers/CMSIS/Include
DEFINES := -DUSE_HAL_DRIVER -DSTM32G474xx
CFLAGS := $(CPU) $(INCLUDES) $(DEFINES) -std=gnu11 -Wall -Wextra -Werror \
          -ffunction-sections -fdata-sections -fno-common -MMD -MP -fstack-usage
ifeq ($(CONFIG),Debug)
CFLAGS += -O0 -g3 -DDEBUG
else ifeq ($(CONFIG),Release)
CFLAGS += -Os -g0
else
$(error CONFIG must be Debug or Release)
endif
SOURCES := $(wildcard Core/Src/*.c) $(wildcard Drivers/STM32G4xx_HAL_Driver/Src/*.c)
ASM_SOURCES := Core/Startup/startup_stm32g474retx.s
OBJECTS := $(patsubst %.c,$(BUILD_DIR)/%.o,$(SOURCES)) \
           $(patsubst %.s,$(BUILD_DIR)/%.o,$(ASM_SOURCES))
LDFLAGS := $(CPU) -TSTM32G474RETX_FLASH.ld --specs=nano.specs --specs=nosys.specs \
           -Wl,--gc-sections -Wl,--print-memory-usage -Wl,-Map=$(TARGET).map

.PHONY: all size host-test
all: $(TARGET).elf $(TARGET).hex $(TARGET).bin size

$(BUILD_DIR)/%.o: %.c
	@mkdir -p "$(@D)"
	"$(CC)" $(CFLAGS) -c "$<" -o "$@"

$(BUILD_DIR)/%.o: %.s
	@mkdir -p "$(@D)"
	"$(CC)" $(CPU) $(DEFINES) -g3 -x assembler-with-cpp -c "$<" -o "$@"

$(TARGET).elf: $(OBJECTS) STM32G474RETX_FLASH.ld Makefile
	@mkdir -p "$(@D)"
	"$(CC)" $(OBJECTS) $(LDFLAGS) -Wl,--start-group -lc -lm -Wl,--end-group -o "$@"

$(TARGET).hex: $(TARGET).elf
	"$(OBJCOPY)" -O ihex "$<" "$@"

$(TARGET).bin: $(TARGET).elf
	"$(OBJCOPY)" -O binary "$<" "$@"

size: $(TARGET).elf
	"$(SIZE)" "$<"

host-test:
	HOST_CC="$(HOST_CC)" sh tests/run_host_tests.sh

-include $(OBJECTS:.o=.d)
