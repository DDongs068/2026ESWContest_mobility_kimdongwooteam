#include "ethernet_test.h"
#include "bms_app.h"
#include "main.h"
#include "demo_stream.h"
#include "safety_supervisor.h"
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

volatile Ethernet_Control g_eth_control;
volatile Ethernet_Status g_eth;
static Ethernet_Control active;
static uint32_t last_attempt, pending_tick, last_init, last_link;
static int init_attempted;
static uint32_t motor_revision, motor_pending_tick, motor_reply_id;
static uint16_t motor_reply_port;
static int motor_pending, motor_reply_wait, motor_reply_result;
static int motor_setup(void);
static int motor_update(void);

static int read_bytes(uint8_t b, uint16_t a, uint8_t *p, uint16_t n)
{ return Board_EthernetTransfer(a, b, 0, p, n); }
static int write_bytes(uint8_t b, uint16_t a, const uint8_t *p, uint16_t n)
{ return Board_EthernetTransfer(a, b, 1, (uint8_t *)p, n); }
static int write8(uint8_t b, uint16_t a, uint8_t v)
{ return write_bytes(b, a, &v, 1); }
static int read16(uint16_t a, uint16_t *v)
{
    uint8_t p[2];
    if (read_bytes(1, a, p, 2)) return -1;
    *v = (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
    return 0;
}
static int write16(uint16_t a, uint16_t v)
{
    uint8_t p[2] = {(uint8_t)(v >> 8), (uint8_t)v};
    return write_bytes(1, a, p, 2);
}
static int command(uint8_t c)
{
    uint8_t value;
    uint32_t start = HAL_GetTick();
    if (write8(1, 1, c)) return -1;
    do {
        if (read_bytes(1, 1, &value, 1)) return -1;
        if (!value) return 0;
    } while (HAL_GetTick() - start < 10u);
    return -3;
}
static void failed(int result)
{
    g_eth.last_result = result;
    ++g_eth.error_count;
    g_eth.pending = 0;
    if (result != -4 && result != -6) g_eth.ready = 0;
}
static int setup(void)
{
    uint8_t version, status, check[18], network[18];
    int rc;
    active = g_eth_control;
    g_eth.ready = g_eth.pending = g_eth.link_up = 0;
    Board_EthernetReset();
    if (read_bytes(0, 0x39, &version, 1)) return -1;
    g_eth.version = version;
    if (version != 4) return -2;
    memcpy(network, active.gateway, 4);
    memcpy(network+4, active.subnet, 4);
    memcpy(network+8, active.mac, 6);
    memcpy(network+14, active.ip, 4);
    if (write_bytes(0, 1, network, sizeof network) ||
        read_bytes(0, 1, check, sizeof check)) return -1;
    if (memcmp(check, network, sizeof check)) return -7;
    const uint8_t retry[3] = {0x07, 0xD0, 1};
    if (write_bytes(0, 0x19, retry, 3)) return -1;
    rc = command(0x10);
    if (rc) return rc;
    if (write8(1, 0x1E, 2) || write8(1, 0x1F, 2) ||
        write8(1, 0, 2) || write16(4, active.local_port) ||
        write_bytes(1, 0x0C, active.peer_ip, 4) ||
        write16(0x10, active.peer_port) || write8(1, 2, 0xFF)) return -1;
    rc = command(1);
    if (rc) return rc;
    uint32_t start = HAL_GetTick();
    do {
        if (read_bytes(1, 3, &status, 1)) return -1;
        g_eth.socket_status = status;
        if (status == 0x22) {
            rc = motor_setup();
            if (rc) return rc;
            g_eth.ready = 1; return 0;
        }
    } while (HAL_GetTick() - start < 10u);
    return -3;
}

static uint16_t get16(const uint8_t *p)
{ return (uint16_t)(((uint16_t)p[0] << 8) | p[1]); }

static int rx_read(uint16_t pointer, uint8_t *data, uint16_t length)
{
    uint16_t offset = pointer & 2047u;
    uint16_t first = (uint16_t)(2048u - offset);
    if (first > length) first = length;
    if (read_bytes(3, offset, data, first)) return -1;
    if (length > first && read_bytes(3, 0, data + first, (uint16_t)(length-first))) return -1;
    return 0;
}

static int receive_ai(void)
{
    for (unsigned packet = 0u; packet < 4u; ++packet) {
        uint16_t size1 = 0u, size2 = 0u, pointer, length;
        uint8_t header[8], message[6];
        int stable = 0, result;
        for (unsigned n = 0u; n < 4u; ++n) {
            if (read16(0x26u, &size1) || read16(0x26u, &size2)) return -1;
            if (size1 == size2) { stable = 1; break; }
        }
        if (!stable || size1 < sizeof header) return 0;
        if (size1 > 2048u) return -8;
        if (read16(0x28u, &pointer) || rx_read(pointer, header, sizeof header)) return -1;
        length = get16(header + 6);
        if (length > 2040u) return -8;
        if ((uint32_t)length + sizeof header > size1) return 0;
        result = 1;
        if (!g_eth_control.ai_rx_enable) result = 3;
        else if (memcmp(header, active.peer_ip, 4)) result = 2;
        else if (length >= 4u && length <= sizeof message) {
            if (rx_read((uint16_t)(pointer + 8u), message, length)) return -1;
            if (!memcmp(message, "AI:", 3) &&
                (message[3] == '0' || message[3] == '1') &&
                (length == 4u || (length == 5u && message[4] == '\n') ||
                 (length == 6u && message[4] == '\r' && message[5] == '\n')))
                result = 0;
        }
        if (write16(0x28u, (uint16_t)(pointer + 8u + length))) return -1;
        int rc = command(0x40u);
        if (rc) return rc;
        g_eth.last_rx_result = result;
        if (result) ++g_eth.rx_rejected;
        else {
            if (message[3] == '1' && !g_control.ai_alarm) {
                g_control.off = 1u; ++motor_revision;
            }
            g_control.ai_alarm = (uint32_t)(message[3] - '0');
            g_eth.ai_rx_received = 1u;
            g_eth.last_ai_tick = HAL_GetTick();
            ++g_eth.rx_count;
        }
    }
    return 0;
}

static int mread16(uint16_t address, uint16_t *value)
{
    uint8_t p[2];
    if (read_bytes(5, address, p, 2)) return -1;
    *value = get16(p); return 0;
}
static int mwrite16(uint16_t address, uint16_t value)
{
    uint8_t p[2] = {(uint8_t)(value >> 8), (uint8_t)value};
    return write_bytes(5, address, p, 2);
}
static int mcommand(uint8_t cmd)
{
    uint8_t value;
    uint32_t start = HAL_GetTick();
    if (write8(5, 1, cmd)) return -1;
    do {
        if (read_bytes(5, 1, &value, 1)) return -1;
        if (!value) return 0;
    } while (HAL_GetTick()-start < 10u);
    return -3;
}
static int motor_setup(void)
{
    motor_pending = motor_reply_wait = 0;
    int rc = mcommand(0x10);
    if (rc) return rc;
    if (write8(5, 0x1E, 2) || write8(5, 0x1F, 2) || write8(5, 0, 2) ||
        mwrite16(4, active.motor_port) || write8(5, 2, 0xFF)) return -1;
    rc = mcommand(1);
    if (rc) return rc;
    uint8_t status;
    uint32_t start=HAL_GetTick();
    do {
        if (read_bytes(5, 3, &status, 1)) return -1;
        if (status==0x22u) return 0;
    } while (HAL_GetTick()-start<10u);
    return -3;
}
static int mring(uint16_t pointer, uint8_t *data, uint16_t length, int write)
{
    uint16_t offset = pointer & 2047u, first = (uint16_t)(2048u-offset);
    if (first > length) first = length;
    if (Board_EthernetTransfer(offset, write ? 6 : 7, write, data, first)) return -1;
    if (length > first && Board_EthernetTransfer(0, write ? 6 : 7, write,
                                                data+first, (uint16_t)(length-first))) return -1;
    return 0;
}
static int decimal(const char **input, uint32_t *value)
{
    const char *p = *input;
    uint32_t v = 0;
    if (*p < '0' || *p > '9') return -1;
    do {
        unsigned digit = (unsigned)(*p-'0');
        if (v > (UINT32_MAX-digit)/10u) return -1;
        v = v*10u+digit; ++p;
    } while (*p >= '0' && *p <= '9');
    *input = p; *value = v; return 0;
}
static int motor_receive(void)
{
    for (unsigned n=0; n<4; ++n) {
        uint16_t size1=0, size2=0, pointer, length;
        uint8_t header[8]; char message[224];
        int stable=0;
        for (unsigned i=0; i<4; ++i) {
            if (mread16(0x26, &size1) || mread16(0x26, &size2)) return -1;
            if (size1==size2) { stable=1; break; }
        }
        if (!stable || size1<8u) return 0;
        if (size1>2048u) return -8;
        if (mread16(0x28, &pointer) || mring(pointer, header, 8, 0)) return -1;
        length=get16(header+6);
        if (length>2040u) return -8;
        if ((uint32_t)length+8u>size1) return 0;
        int readable=g_eth_control.motor_rx_enable && !memcmp(header,active.peer_ip,4) &&
            length>0 && length<sizeof message;
        if (readable) {
            if (mring((uint16_t)(pointer+8u), (uint8_t *)message, length, 0)) return -1;
            if (memchr(message,0,length)) readable=0;
            message[length]=0;
        }
        if (mwrite16(0x28,(uint16_t)(pointer+8u+length))) return -1;
        int rc=mcommand(0x40); if (rc) return rc;
        if (!readable || strncmp(message,"MC1 ",4)) continue;
        const char *p=message+4; uint32_t id, revision;
        if (decimal(&p,&id) || *p++!=' ') continue;
        int result=0;
        if (!strcmp(p,"STATUS")) {   }
        else if (!strcmp(p,"ACK")) result=Safety_Rearm();
        else if (!strcmp(p,"STREAM ON")) g_eth_control.enable=1;
        else if (!strcmp(p,"STREAM OFF")) g_eth_control.enable=0;
        else if (!strcmp(p,"BALANCE OFF")) g_balance.stop=1;
        else if (!strcmp(p,"BALANCE ON")) {
            if (g_safety.active || g_safety.latched || g_control.ai_alarm ||
                g_control.off || g_bms.stage || g_bms.power_gpio_mask) result=-50;
            else g_balance.start=1;
        }
        else if (!strcmp(p,"REPLAY STOP")) { ++motor_revision; Demo_Stop(); }
        else if (!strncmp(p,"REPLAY START ",13)) {
            p+=13; uint32_t session;
            if (decimal(&p,&session) || *p) continue;
            if (!g_demo.mode || g_demo.session!=session) ++motor_revision;
            result=Demo_Start(session); g_eth_control.enable=1;
        }
        else if (!strncmp(p,"REPLAY FRAME ",13)) result=Demo_ReceiveHex(p+13);
        else if (!strncmp(p,"RESULT ",7)) {
            uint32_t session,sequence,alarm; p+=7;
            if (decimal(&p,&session) || *p++!=' ' || decimal(&p,&sequence) ||
                *p++!=' ' || decimal(&p,&alarm) || *p || alarm>1u) continue;
            if (!g_eth_control.ai_rx_enable || !g_demo.mode || !g_demo.valid || session!=g_demo.session ||
                sequence>g_demo.last_sequence || g_demo.last_sequence-sequence>100u) result=-44;
            else {
                if (alarm && !g_control.ai_alarm) { g_control.off=1; ++motor_revision; }
                g_control.ai_alarm=alarm;
                g_eth.ai_rx_received=1; g_eth.last_ai_tick=HAL_GetTick();
            }
        }
        else if (!strcmp(p,"OFF")) {
            ++motor_revision; g_control.off=1u;
        } else if (!strncmp(p,"ON ",3)) {
            p+=3;
            if (decimal(&p,&revision) || *p) continue;
            if (revision!=motor_revision) result=-30;
            else if (g_safety.latched) result=-50;
            else {
                ++motor_revision;
                result=BMS_RemoteMotorOn();
            }
        } else continue;
        motor_reply_id=id; motor_reply_port=get16(header+4);
        motor_reply_result=result; motor_reply_wait=1;
    }
    return 0;
}
static int motor_update(void)
{
    uint8_t status;
    if (read_bytes(5,3,&status,1)) return -1;
    if (status!=0x22u) return motor_setup();
    int rc=motor_receive(); if (rc) return rc;
    uint32_t now=HAL_GetTick();
    if (motor_pending) {
        uint8_t ir;
        if (read_bytes(5,2,&ir,1)) return -1;
        if (ir & 0x18u) {
            if (write8(5,2,(uint8_t)(ir & 0x18u))) return -1;
            motor_pending=0;
        } else if (now-motor_pending_tick>=1000u) return motor_setup();
        else return 0;
    }
    if (!motor_reply_wait || !g_eth.link_up) return 0;
    BMS_Status b=g_bms;
    int32_t pack_mv=-1;
    if (b.data_valid && !b.sample_result && isfinite(b.sensor.pack_V) &&
        b.sensor.pack_V>=0 && b.sensor.pack_V<100.0f)
        pack_mv=(int32_t)(b.sensor.pack_V*1000.0f+0.5f);
    uint8_t data[640];
    int count=snprintf((char *)data,sizeof data,
        "{\"id\":%lu,\"rc\":%d,\"rev\":%lu,\"remote\":%ld,\"stage\":%lu,"
        "\"m1\":%ld,\"m2\":%ld,\"gpio\":%u,\"ai\":%lu,\"estop\":%u,"
        "\"fault\":%u,\"fet\":%u,\"valid\":%u,\"pack_mV\":%ld,"
        "\"sample_rc\":%d,\"fet_on_rc\":%d,\"fet_off_rc\":%d,"
        "\"safe_active\":%lu,\"safe_latched\":%lu,\"replay\":%lu,\"session\":%lu,"
        "\"queued\":%lu,\"played\":%lu,\"demo_rc\":%ld,\"baseline\":%lu,"
        "\"pa\":%u,\"pb\":%u,\"profile_rc\":%ld,\"bal_mask\":%lu,"
        "\"bal_rc\":%ld,\"bal_run\":%lu}\n",
        (unsigned long)motor_reply_id,motor_reply_result,(unsigned long)motor_revision,
        (long)b.remote_motor_result,(unsigned long)b.stage,
        (long)g_motor.pwm1_permille,(long)g_motor.pwm2_permille,(unsigned)b.power_gpio_mask,
        (unsigned long)g_control.ai_alarm,(unsigned)b.estop_ok,(unsigned)b.motor_fault_mask,
        (unsigned)b.sensor.fet_status,(unsigned)b.data_valid,(long)pack_mv,
        b.sample_result,b.fet_on_result,b.fet_off_result,
        (unsigned long)g_safety.active,(unsigned long)g_safety.latched,
        (unsigned long)g_demo.mode,(unsigned long)g_demo.session,
        (unsigned long)g_demo.queued,(unsigned long)g_demo.played,
        (long)g_demo.last_result,(unsigned long)g_demo.baseline_flags,
        (unsigned)g_safety.profile.enabled[0],(unsigned)g_safety.profile.enabled[1],
        (long)g_safety.profile_result,(unsigned long)g_balance.active_mask,
        (long)g_balance.result,(unsigned long)g_balance.running);
    if (count<0 || count>=(int)sizeof data) return -9;
    uint16_t free1,free2,pointer;
    if (mread16(0x20,&free1) || mread16(0x20,&free2)) return -1;
    if (free1!=free2 || free1<(unsigned)count) return 0;
    if (write_bytes(5,0x0C,active.peer_ip,4) || mwrite16(0x10,motor_reply_port) ||
        mread16(0x24,&pointer) || write8(5,2,0x18) ||
        mring(pointer,data,(uint16_t)count,1) ||
        mwrite16(0x24,(uint16_t)(pointer+count))) return -1;
    rc=mcommand(0x20); if (rc) return rc;
    motor_pending=1; motor_pending_tick=HAL_GetTick(); motor_reply_wait=0;
    return 0;
}

static int32_t scaled(float value, float factor)
{
    double v = (double)value * factor;
    if (!isfinite(v) || v > INT32_MAX || v <= INT32_MIN) return INT32_MIN;
    return (int32_t)(v < 0 ? v - 0.5 : v + 0.5);
}
uint16_t Ethernet_Test_Encode(uint8_t p[ETH_TEST_PACKET_SIZE], uint32_t sequence)
{
    BMS_Status b = g_bms;
    uint32_t balance=(g_balance.active_mask&7u)|((g_balance.active_mask&16u)>>1);
    int replay=Demo_Sample(&b,&balance);
    int v2=g_eth_control.telemetry_v2 || replay;
    int32_t values[12];
    unsigned valid = b.data_valid && b.sample_result == 0;
    for (unsigned i = 0; i < 4; ++i) {
        values[i] = scaled(b.sensor.cell_V[i], 1000);
        values[4+i] = scaled(b.sensor.temp_C[i], 1000);
    }
    values[8] = scaled(b.sensor.current_A, 1000);
    values[9] = scaled(b.sensor.stack_V, 1000);
    values[10] = scaled(b.sensor.pack_V, 1000);
    values[11] = scaled(b.sensor.ld_V, 1000);
    for (unsigned i = 0; i < 12; ++i) if (values[i] == INT32_MIN) valid = 0;
    int n;
    if (v2) n=snprintf((char *)p,ETH_TEST_PACKET_SIZE,"BMS2,%u,%lu,%lu,%lu,%u",
        replay?1u:0u,(unsigned long)(replay?g_demo.session:0u),
        (unsigned long)(replay?g_demo.last_sequence:sequence),(unsigned long)b.sample_tick,valid);
    else n=snprintf((char *)p,ETH_TEST_PACKET_SIZE,"%lu,%lu,%u",
        (unsigned long)sequence,(unsigned long)b.sample_tick,valid);
    if (n < 0 || n >= (int)ETH_TEST_PACKET_SIZE) return 0;
    unsigned used = (unsigned)n;
    for (unsigned i = 0; i < 12; ++i) {
        uint32_t magnitude = values[i] < 0 ? (uint32_t)(-(int64_t)values[i]) : (uint32_t)values[i];
        if (!valid) n = snprintf((char *)p + used, ETH_TEST_PACKET_SIZE-used, ",NA");
        else n = snprintf((char *)p + used, ETH_TEST_PACKET_SIZE-used, ",%s%lu.%03lu",
                          values[i] < 0 ? "-" : "", (unsigned long)(magnitude/1000u),
                          (unsigned long)(magnitude%1000u));
        if (n < 0 || (unsigned)n >= ETH_TEST_PACKET_SIZE-used) return 0;
        used += (unsigned)n;
    }
    if (v2) {
        n=snprintf((char *)p+used,ETH_TEST_PACKET_SIZE-used,
            ",%u,%u,%u,%u,%lu,%lu,%lu,%lu,%lu,%lu",
            (unsigned)(balance&1u),(unsigned)((balance>>1)&1u),
            (unsigned)((balance>>2)&1u),(unsigned)((balance>>3)&1u),
            (unsigned long)(replay?g_demo.baseline_flags:0u),
            (unsigned long)(replay?g_demo.spec_overcharge:0u),
            (unsigned long)g_safety.active,(unsigned long)g_safety.latched,
            (unsigned long)g_control.ai_alarm,(unsigned long)g_safety.fet);
        if (n<0 || (unsigned)n>=ETH_TEST_PACKET_SIZE-used) return 0;
        used+=(unsigned)n;
    }
    if (used + 1u >= ETH_TEST_PACKET_SIZE) return 0;
    p[used++] = '\n'; p[used] = 0;
    return (uint16_t)used;
}

void Ethernet_Test_Init(void)
{
    g_eth_control = (Ethernet_Control){.period_ms=100, .ai_rx_enable=1, .telemetry_v2=1, .motor_rx_enable=1, .motor_port=5002,
        .ip={192,168,10,20}, .peer_ip={192,168,10,1}, .subnet={255,255,255,0},
        .gateway={192,168,10,1}, .mac={0x02,0x42,0x4D,0x53,0x00,0x01},
        .local_port=5000, .peer_port=5001};
    g_eth = (Ethernet_Status){0};
    Demo_Init();
    motor_revision=0; motor_pending=motor_reply_wait=0;
    last_attempt = HAL_GetTick()-g_eth_control.period_ms;
    last_init = last_link = 0; init_attempted = 0;
}

void Ethernet_Test_Update(void)
{
    uint32_t now = HAL_GetTick();
    int wanted = g_eth_control.enable || g_eth_control.send_once;
    if (g_eth_control.reinit || (!g_eth.ready && (wanted || g_eth_control.ai_rx_enable || g_eth_control.motor_rx_enable) &&
        (!init_attempted || now-last_init >= 1000u))) {
        g_eth_control.reinit = 0;
        init_attempted = 1; last_init = now;
        int rc = setup();
        if (rc) { failed(rc); return; }
        g_eth.last_result = 0;
        last_link = now-250u;
    }
    if (!g_eth.ready) return;
    uint8_t status;
    if (read_bytes(1, 3, &status, 1)) { failed(-1); return; }
    g_eth.socket_status = status;
    if (status != 0x22u) { failed(-3); return; }
    if (now-last_link >= 250u || wanted) {
        uint8_t phy;
        last_link = now;
        if (read_bytes(0, 0x2E, &phy, 1)) { failed(-1); return; }
        g_eth.phy = phy; g_eth.link_up = phy & 1u;
    }
    int rx_result = receive_ai();
    if (rx_result) {
        g_eth.last_rx_result = rx_result;
        failed(rx_result);
        return;
    }
    int motor_result = motor_update();
    if (motor_result) { failed(motor_result); return; }
    if (g_eth.pending) {
        uint8_t ir;
        if (read_bytes(1, 2, &ir, 1)) { failed(-1); return; }
        if (ir & 8u) {
            if (write8(1, 2, 8u)) { failed(-1); return; }
            g_eth.pending = 0;
            g_eth.last_result = -5;
            ++g_eth.error_count;
            return;
        }
        if (ir & 0x10u) {
            if (write8(1, 2, 0x10)) { failed(-1); return; }
            g_eth.pending = 0; ++g_eth.tx_count;
            g_eth.last_tx_tick = now; g_eth.last_result = 0;
        } else if (now-pending_tick >= 1000u) { failed(-5); return; }
        else return;
    }
    uint32_t period = g_eth_control.period_ms < 100u ? 100u : g_eth_control.period_ms;
    if (!g_eth_control.send_once && (!g_eth_control.enable || now-last_attempt < period)) return;
    g_eth_control.send_once = 0; last_attempt = now;
    if (!g_eth.link_up) { failed(-4); return; }
    uint16_t free1=0, free2=0, pointer;
    int stable = 0;
    for (unsigned i=0; i<4; ++i) {
        if (read16(0x20, &free1) || read16(0x20, &free2)) { failed(-1); return; }
        if (free1 == free2) { stable = 1; break; }
    }
    uint8_t p[ETH_TEST_PACKET_SIZE];
    uint16_t length = Ethernet_Test_Encode(p, g_eth.sequence + 1u);
    if (!length) { failed(-9); return; }
    if (!stable || free1 < length) { failed(-6); return; }
    ++g_eth.sequence;
    if (read16(0x24, &pointer) || write8(1, 2, 0x18) ||
        write_bytes(2, pointer, p, length) ||
        write16(0x24, (uint16_t)(pointer+length))) { failed(-1); return; }
    int rc = command(0x20);
    if (rc) { failed(rc); return; }
    pending_tick = HAL_GetTick(); g_eth.pending = 1; g_eth.last_result = 1;
}
