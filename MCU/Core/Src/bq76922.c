#include "bq76922.h"
#include <string.h>

/* BQ76922 SLUUCG7: 7-bit address 0x08, no I2C CRC.
 * Synchronous main-loop driver; the MAC transfer buffer has a single owner.
 * Errors: -1 HAL I/O, -2 completion/echo, -3 length, -4 checksum,
 *         -5 readback, -6 incompatible NTC setting, -7 argument.
 * Configuration changes are RAM-only. */
volatile uint32_t bq_i2c_ok, bq_i2c_errors, bq_last_hal_error;
volatile int bq_last_error;
static I2C_HandleTypeDef *bq_bus;
static const uint16_t ntc_address[4] = {0x92FDu, 0x92FEu, 0x92FCu, 0x92FAu};

static int result(int code)
{
    bq_last_error = code;
    return code;
}

static uint16_t u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static int16_t i16(const uint8_t *p)
{
    uint16_t value = u16(p);
    return (int16_t)((int32_t)value - ((value & 0x8000u) ? 65536 : 0));
}

static int read_bytes(uint8_t reg, uint8_t *data, uint16_t count)
{
    if (!bq_bus || !data || !count) return result(-7);
    if (HAL_I2C_Mem_Read(bq_bus, 0x10u, reg, I2C_MEMADD_SIZE_8BIT,
                         data, count, 25u) != HAL_OK) {
        ++bq_i2c_errors;
        bq_last_hal_error = HAL_I2C_GetError(bq_bus);
        return result(-1);
    }
    ++bq_i2c_ok;
    bq_last_hal_error = 0u;
    return 0;
}

static int write_bytes(uint8_t reg, const uint8_t *data, uint16_t count)
{
    if (!bq_bus || !data || !count) return result(-7);
    if (HAL_I2C_Mem_Write(bq_bus, 0x10u, reg, I2C_MEMADD_SIZE_8BIT,
                          (uint8_t *)data, count, 25u) != HAL_OK) {
        ++bq_i2c_errors;
        bq_last_hal_error = HAL_I2C_GetError(bq_bus);
        return result(-1);
    }
    ++bq_i2c_ok;
    bq_last_hal_error = 0u;
    return 0;
}

static int read_word(uint8_t reg, uint16_t *value)
{
    uint8_t data[2];
    if (read_bytes(reg, data, 2u)) return bq_last_error;
    *value = u16(data);
    return 0;
}

static int command(uint16_t value)
{
    uint8_t data[2] = {(uint8_t)value, (uint8_t)(value >> 8)};
    if (write_bytes(0x3Eu, data, 2u)) return bq_last_error;
    HAL_Delay(2u);
    return 0;
}

static int wait_echo(uint16_t address)
{
    uint16_t echo;
    for (unsigned n = 0u; n < 30u; ++n) {
        HAL_Delay(1u);
        if (read_word(0x3Eu, &echo)) return bq_last_error;
        if (echo == address) return 0;
        if (echo != 0xFFFFu) return result(-2);
    }
    return result(-2);
}

/* The checksum covers address + the entire returned buffer, not just the
 * requested field. Data-memory reads may populate all 32 transfer bytes. */
static int memory_read(uint16_t address, uint8_t *out, uint8_t count)
{
    uint8_t data[32], total, checksum, sum, size;
    if (!out || !count || count > 32u) return result(-7);
    if (command(address) || wait_echo(address)) return bq_last_error;
    if (read_bytes(0x61u, &total, 1u)) return bq_last_error;
    if (total < 5u || total > 36u) return result(-3);
    size = (uint8_t)(total - 4u);
    if (size < count || (address < 0x9000u && size != count)) return result(-3);
    if (read_bytes(0x40u, data, size) || read_bytes(0x60u, &checksum, 1u)) return bq_last_error;
    sum = (uint8_t)((address & 0xFFu) + (address >> 8));
    for (unsigned i = 0u; i < size; ++i) sum = (uint8_t)(sum + data[i]);
    if ((uint8_t)(sum ^ 0xFFu) != checksum) return result(-4);
    memcpy(out, data, count);
    return 0;
}

static int memory_write(uint16_t address, uint8_t count, uint32_t value)
{
    uint8_t data[4], verify[4], trailer[2];
    uint8_t sum = (uint8_t)((address & 0xFFu) + (address >> 8));
    if (!count || count > 4u) return result(-7);
    for (unsigned i = 0u; i < count; ++i) {
        data[i] = (uint8_t)(value >> (8u * i));
        sum = (uint8_t)(sum + data[i]);
    }
    /* Let the address request finish populating old data before replacing it. */
    if (command(address) || wait_echo(address)) return bq_last_error;
    trailer[0] = (uint8_t)(sum ^ 0xFFu);
    trailer[1] = (uint8_t)(count + 4u);
    if (write_bytes(0x40u, data, count) || write_bytes(0x60u, trailer, 2u)) return bq_last_error;
    HAL_Delay(2u);
    if (memory_read(address, verify, count)) return bq_last_error;
    if (memcmp(data, verify, count)) return result(-5);
    return 0;
}

static int wait_config(uint16_t expected)
{
    uint16_t status;
    for (unsigned n = 0u; n < 30u; ++n) {
        if (read_word(0x12u, &status)) return bq_last_error;
        if ((status & 1u) == expected) return 0;
        HAL_Delay(1u);
    }
    return result(-2);
}

static int is_ntc(uint8_t config)
{
    /* Thermistor mode, a temperature measurement type, a polynomial and pullup. */
    return (config & 3u) == 3u && (config & 12u) != 0u &&
           (config & 48u) != 48u && (config & 192u) < 128u;
}

void BQ_Init(I2C_HandleTypeDef *i2c)
{
    bq_bus = i2c;
    bq_i2c_ok = bq_i2c_errors = bq_last_hal_error = 0u;
    bq_last_error = 0;
}

int BQ_ReadID(uint16_t *device_id)
{
    uint8_t data[2];
    if (!device_id) return result(-7);
    if (memory_read(0x0001u, data, 2u)) return bq_last_error;
    *device_id = u16(data);
    return result(0);
}

int BQ_Configure(void)
{
    uint8_t ntc[4], data[2], da;
    uint16_t status, power;
    uint32_t gain_bits, capacity_bits;
    float gain = 7.4768f / 10.0f; /* Nominal 10-milliohm board shunt. */
    float capacity = (float)((double)gain * 298261.6178);
    int error = 0;

    if (sizeof(float) != 4u) return result(-7);
    if (read_word(0x12u, &status)) return bq_last_error;
    /* Complete setup even if an MCU-only reset left the BMIC in CFGUPDATE. */
    if (!(status & 1u) && (command(0x0090u) || wait_config(1u))) {
        error = bq_last_error;
        goto finish;
    }
    for (unsigned i = 0u; i < 4u; ++i) {
        if (memory_read(ntc_address[i], &ntc[i], 1u)) { error = bq_last_error; goto finish; }
        if ((ntc[i] & 3u) == 0u) ntc[i] = 0x0Bu; /* 18K model, reported only. */
        else if (!is_ntc(ntc[i])) { error = -6; goto finish; }
    }
    if (memory_read(0x9234u, data, 2u)) { error = bq_last_error; goto finish; }
    power = (uint16_t)((u16(data) | 0x1000u) & 0xFEFFu); /* TS2 thermistor, SLEEP off. */
    if (memory_read(0x9303u, &da, 1u)) { error = bq_last_error; goto finish; }
    da = (uint8_t)((da & 0xF8u) | 5u); /* 1mA/userA and 10mV/userV. */
    memcpy(&gain_bits, &gain, 4u);
    memcpy(&capacity_bits, &capacity, 4u);
    if (memory_write(0x92FBu, 1u, 0x42u) || /* DFETOFF: active-high BOTHOFF. */
        memory_write(0x9304u, 2u, 0x0017u) || /* Physical cells: channels 1,2,3,5. */
        memory_write(0x9303u, 1u, da) ||
        memory_write(0x9234u, 2u, power) ||
        memory_write(0x91A8u, 4u, gain_bits) ||
        memory_write(0x91ACu, 4u, capacity_bits)) {
        error = bq_last_error;
        goto finish;
    }
    for (unsigned i = 0u; i < 4u; ++i) {
        if (memory_write(ntc_address[i], 1u, ntc[i])) { error = bq_last_error; break; }
    }
finish:
    /* Always attempt to resume normal BMIC operation after a partial setup. */
    if (command(0x0092u) || wait_config(0u)) return bq_last_error;
    if (error) return result(error);
    if (command(0x009Au)) return bq_last_error; /* Disable SLEEP for periodic measurements. */
    return result(0);
}

int BQ_Read(BQ_Data *data)
{
    const uint8_t cell_reg[4] = {0x14u, 0x16u, 0x18u, 0x1Cu};
    const uint8_t temp_reg[4] = {0x70u, 0x72u, 0x6Eu, 0x6Au};
    const float current_unit[4] = {0.0001f, 0.001f, 0.01f, 0.1f};
    BQ_Data value = {0};
    uint8_t raw[2], da, ntc[4];
    float volts;
    if (!data) return result(-7);
    if (memory_read(0x9303u, &da, 1u)) return bq_last_error;
    for (unsigned i = 0u; i < 4u; ++i) {
        if (memory_read(ntc_address[i], &ntc[i], 1u)) return bq_last_error;
    }
    volts = (da & 4u) ? 0.01f : 0.001f;
    for (unsigned i = 0u; i < 4u; ++i) {
        if (read_bytes(cell_reg[i], raw, 2u)) return bq_last_error;
        value.cell_V[i] = (float)i16(raw) * 0.001f;
    }
    if (read_bytes(0x34u, raw, 2u)) return bq_last_error;
    value.stack_V = (float)i16(raw) * volts;
    if (read_bytes(0x36u, raw, 2u)) return bq_last_error;
    value.pack_V = (float)i16(raw) * volts;
    if (read_bytes(0x38u, raw, 2u)) return bq_last_error;
    value.ld_V = (float)i16(raw) * volts;
    if (read_bytes(0x3Au, raw, 2u)) return bq_last_error;
    value.current_A = (float)i16(raw) * current_unit[da & 3u];
    for (unsigned i = 0u; i < 4u; ++i) {
        if (read_bytes(temp_reg[i], raw, 2u)) return bq_last_error;
        value.temp_C[i] = (float)i16(raw) * 0.1f - 273.15f;
        if (is_ntc(ntc[i])) value.ntc_valid_mask |= (uint8_t)(1u << i);
    }
    if (memory_read(0x0057u, raw, 2u)) return bq_last_error;
    value.mfg_status = u16(raw);
    if (read_bytes(0x7Fu, &value.fet_status, 1u)) return bq_last_error;
    for (unsigned i = 0u; i < 3u; ++i) {
        if (read_bytes((uint8_t)(0x03u + 2u * i), &value.safety_status[i], 1u)) return bq_last_error;
    }
    for (unsigned i = 0u; i < 4u; ++i) {
        if (read_bytes((uint8_t)(0x0Bu + 2u * i), &value.pf_status[i], 1u)) return bq_last_error;
    }
    if (read_word(0x12u, &value.battery_status)) return bq_last_error;
    *data = value; /* A failed transfer leaves the caller's previous sample intact. */
    return result(0);
}

int BQ_EnableFETs(void)
{
    uint8_t data[2];
    if (memory_read(0x0057u, data, 2u)) return bq_last_error;
    if (!(u16(data) & 0x10u)) {
        /* FET_ENABLE toggles FET_EN; do not send it blindly or retry blindly. */
        if (command(0x0022u) || memory_read(0x0057u, data, 2u)) return bq_last_error;
        if (!(u16(data) & 0x10u)) return result(-5);
    }
    if (command(0x0096u)) return bq_last_error;
    return result(0);
}

int BQ_DisableFETs(void)
{
    if (command(0x0095u)) return bq_last_error;
    return result(0);
}

int BQ_BalanceConfig(uint8_t *config, uint8_t *interval_s)
{
    if (!config || !interval_s) return result(-7);
    if (memory_read(0x9335u, config, 1u) ||
        memory_read(0x9339u, interval_s, 1u)) return bq_last_error;
    return result(0);
}
int BQ_BalanceStatus(uint16_t *mask, uint16_t *seconds)
{
    uint8_t data[2];
    if (!mask || !seconds) return result(-7);
    if (memory_read(0x0083u, data, 2u)) return bq_last_error;
    *mask = u16(data);
    if (memory_read(0x0085u, data, 2u)) return bq_last_error;
    *seconds = u16(data);
    return result(0);
}
int BQ_BalanceSet(uint16_t mask)
{
    uint8_t data[2] = {(uint8_t)mask, (uint8_t)(mask >> 8)};
    uint8_t trailer[2] = {(uint8_t)((0x83u + data[0] + data[1]) ^ 0xFFu), 6u};
    if ((mask & ~0x17u) || (mask && (mask & (mask-1u)))) return result(-7);
    if (command(0x0083u) || wait_echo(0x0083u) ||
        write_bytes(0x40u, data, 2u) || write_bytes(0x60u, trailer, 2u))
        return bq_last_error;
    HAL_Delay(2u);
    /* CB_ACTIVE_CELLS can take about1s to change; poll separately. */
    return result(0);
}

int BQ_ReadProtectionProfile(BQ_ProtectionProfile *profile)
{
    BQ_ProtectionProfile value={0};
    if (!profile) return result(-7);
    if (memory_read(0x9261u,value.enabled,3u) ||
        memory_read(0x9265u,value.chg_map,3u) ||
        memory_read(0x9269u,value.dsg_map,3u) ||
        memory_read(0x9275u,&value.cuv_code,1u) ||
        memory_read(0x9278u,&value.cov_code,1u) ||
        memory_read(0x9286u,&value.scd_code,1u) ||
        memory_read(0x9287u,&value.scd_delay_code,1u)) return bq_last_error;
    *profile=value;
    return result(0);
}
