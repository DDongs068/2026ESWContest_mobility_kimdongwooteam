#include "demo_stream.h"
#include <string.h>
#include <limits.h>

typedef struct {
    uint32_t sequence, ms, balance;
    int32_t cell[4], temp[4], current, stack, pack, ld;
} Frame;
volatile Demo_Status g_demo;
static Frame queue[DEMO_QUEUE_SIZE], current;
static uint32_t head, count, next_sequence, previous_ms, origin_ms, wall_start, last_input;
static uint32_t have_input, have_output, previous_crc;
static int halted;
static int32_t maxima[100];
static unsigned maxima_count, maxima_head;
static uint32_t u32(const uint8_t *p)
{ return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }
static int32_t i32(const uint8_t *p)
{
    uint32_t v=u32(p);
    return v<=INT32_MAX?(int32_t)v:(int32_t)(-1-(int64_t)(UINT32_MAX-v));
}
static uint32_t crc32(const uint8_t *p, unsigned length)
{
    uint32_t crc=UINT32_MAX;
    for (unsigned i=0;i<length;++i) {
        crc^=p[i];
        for (unsigned b=0;b<8;++b) crc=(crc>>1)^((crc&1u)?0xEDB88320u:0u);
    }
    return crc^UINT32_MAX;
}
static int nibble(char c)
{ return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:-1; }
void Demo_Init(void)
{
    g_demo=(Demo_Status){0}; head=count=next_sequence=have_input=have_output=0;
    maxima_count=maxima_head=0; halted=0;
}
int Demo_Start(uint32_t session)
{
    if (g_demo.mode && g_demo.session==session) return 0;
    Demo_Init(); g_demo.mode=1; g_demo.session=session; last_input=HAL_GetTick();
    g_control.off=1; /* Entry is explicit and starts with physical loads OFF. */
    return 0;
}
void Demo_Stop(void)
{
    Demo_Init(); g_control.off=1; /* Return to live never restarts loads. */
}
int Demo_ReceiveHex(const char *hex)
{
    uint8_t wire[DEMO_WIRE_SIZE]; Frame f;
    int rc=-40;
    if (strlen(hex)!=DEMO_WIRE_SIZE*2u) goto reject;
    for (unsigned i=0;i<sizeof wire;++i) {
        int hi=nibble(hex[i*2]),lo=nibble(hex[i*2+1]);
        if (hi<0||lo<0) goto reject;
        wire[i]=(uint8_t)((hi<<4)|lo);
    }
    if (memcmp(wire,"BDP1",4)||crc32(wire,68)!=u32(wire+68)) goto reject;
    rc=-41;
    if (!g_demo.mode || halted || u32(wire+4)!=g_demo.session) goto reject;
    f.sequence=u32(wire+8); f.ms=u32(wire+12);
    /* Retransmission of the most recently ACKed frame is idempotent. */
    if (have_input && f.sequence==next_sequence-1u && f.ms==previous_ms && u32(wire+68)==previous_crc) {
        ++g_demo.duplicates; return 0;
    }
    rc=-43;
    if (f.sequence!=next_sequence || (have_input && (uint32_t)(f.ms-previous_ms)!=100u)) goto reject;
    for (unsigned i=0;i<4;++i) {
        f.cell[i]=i32(wire+16+i*4); f.temp[i]=i32(wire+32+i*4);
        if (f.cell[i]<0 || f.cell[i]>6000 || f.temp[i]<-40000 || f.temp[i]>125000) goto reject;
    }
    f.current=i32(wire+48); f.stack=i32(wire+52); f.pack=i32(wire+56); f.ld=i32(wire+60);
    f.balance=u32(wire+64);
    if (f.current < -100000 || f.current>100000 || f.stack<0 || f.stack>24000 ||
        f.pack<0 || f.pack>24000 || f.ld<0 || f.ld>24000 || f.balance>15u) goto reject;
    rc=-42;
    if (count>=DEMO_QUEUE_SIZE) goto reject;
    if (!have_input) { origin_ms=f.ms; wall_start=HAL_GetTick(); }
    queue[(head+count)%DEMO_QUEUE_SIZE]=f; ++count; ++next_sequence;
    previous_ms=f.ms; previous_crc=u32(wire+68); have_input=1; last_input=HAL_GetTick();
    ++g_demo.accepted; g_demo.queued=count; g_demo.last_result=0;
    return 0;
reject:
    ++g_demo.rejected; g_demo.last_result=rc; return rc;
}
void Demo_Update(void)
{
    if (!g_demo.mode) return;
    uint32_t now=HAL_GetTick();
    if (count && now-wall_start >= queue[head].ms-origin_ms) {
        current=queue[head]; head=(head+1u)%DEMO_QUEUE_SIZE; --count;
        g_demo.queued=count; ++g_demo.played; g_demo.last_sequence=current.sequence;
        g_demo.source_ms=current.ms; g_demo.valid=1; have_output=1;
        uint32_t flags=0; int32_t maximum=current.cell[0];
        for (unsigned i=0;i<4;++i) {
            if (current.cell[i]>=4250) flags|=1u;
            if (current.cell[i]<=2800) flags|=2u;
            if (current.temp[i]>=60000) flags|=8u;
            if (current.cell[i]>maximum) maximum=current.cell[i];
        }
        if (current.current<=-6000) flags|=4u;
        if (flags && !g_demo.baseline_flags) g_demo.first_baseline_ms=current.ms;
        g_demo.baseline_flags|=flags; /* Latched simulation comparison, not a BMIC register. */
        maxima[maxima_head++%100u]=maximum;
        if (maxima_count<100u) ++maxima_count;
        /* PPT's separate overvoltage rule on a full 10s/100-sample window. */
        if (maxima_count==100u && (g_demo.played-100u)%50u==0u) {
            int32_t sorted[100]; memcpy(sorted,maxima,sizeof sorted);
            for (unsigned i=1;i<100u;++i) {
                int32_t v=sorted[i]; unsigned j=i;
                while (j && sorted[j-1]>v) { sorted[j]=sorted[j-1]; --j; }
                sorted[j]=v;
            }
            g_demo.rule_ready=1;
            g_demo.spec_overcharge=(int64_t)sorted[49]+sorted[50]>8410;
        }
        /* Instantaneous context for diagnostics only; AE router uses window mean. */
        g_demo.route=current.current>30?1u:current.current< -30?2u:0u;
    }
    /* Transport gap cannot be classified as a healthy battery window. */
    if (!count && now-last_input>1000u && !halted) {
        halted=1; g_demo.valid=0; g_demo.last_result=-45; ++g_demo.underruns; g_control.off=1;
    }
}
int Demo_Sample(BMS_Status *sample,uint32_t *balance)
{
    if (!g_demo.mode) return 0;
    memset(sample,0,sizeof *sample); *balance=0;
    sample->sample_tick=g_demo.source_ms;
    sample->data_valid=g_demo.valid && have_output;
    sample->sample_result=sample->data_valid?0:-45;
    if (!sample->data_valid) return 1;
    for (unsigned i=0;i<4;++i) {
        sample->sensor.cell_V[i]=current.cell[i]*0.001f;
        sample->sensor.temp_C[i]=current.temp[i]*0.001f;
    }
    sample->sensor.current_A=current.current*0.001f;
    sample->sensor.stack_V=current.stack*0.001f;
    sample->sensor.pack_V=current.pack*0.001f;
    sample->sensor.ld_V=current.ld*0.001f;
    *balance=current.balance;
    return 1;
}
