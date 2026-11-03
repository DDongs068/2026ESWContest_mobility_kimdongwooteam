#include "bms_demo.h"
#include "main.h"
#include <limits.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

volatile BMS_DemoStatus g_demo;

static unsigned cov_n,cuv_n,ocd_n,ot_n;
static void DemoJudgeReset(void){cov_n=cuv_n=ocd_n=ot_n=0u;}
static unsigned persist(unsigned n,int hit,unsigned hold)
{ return !hit?0u:n<hold?n+1u:hold; }
static uint32_t DemoJudge(const BMS_DemoStatus *s)
{
    int32_t vmax=s->cell_mV[0],vmin=s->cell_mV[0],tmax=s->temp_mC[0];
    for (unsigned i=1;i<4u;++i) {
        if (s->cell_mV[i]>vmax) vmax=s->cell_mV[i];
        if (s->cell_mV[i]<vmin) vmin=s->cell_mV[i];
        if (s->temp_mC[i]>tmax) tmax=s->temp_mC[i];
    }
    cov_n=persist(cov_n,vmax>4250,5u);
    cuv_n=persist(cuv_n,vmin<2800,10u);
    ocd_n=persist(ocd_n,s->current_mA<-6000,1u);
    ot_n =persist(ot_n,tmax>60000,10u);
    return (cov_n>=5u?1u:0u)|(cuv_n>=10u?2u:0u)|(ocd_n>=1u?4u:0u)|(ot_n>=10u?8u:0u);
}

void BMS_DemoInit(void)
{
    DemoJudgeReset();
    g_demo=(BMS_DemoStatus){.emergency=2u,.algorithm_available=1u,.decision_result=0};
}

void BMS_DemoSetRunning(int running)
{
    g_demo.started=running!=0;
}

void BMS_DemoClear(void)
{
    DemoJudgeReset();
    BMS_DemoStatus next={.started=g_demo.started,.emergency=2u,.algorithm_available=1u,
        .accepted=g_demo.accepted,.rejected=g_demo.rejected,
        .duplicates=g_demo.duplicates,.decision_result=0};
    g_demo=next;
}

static int unsigned_field(const char *field,uint32_t *result)
{
    uint32_t value=0;
    if (!*field) return -1;
    do {
        if (*field<'0' || *field>'9') return -1;
        unsigned digit=(unsigned)(*field-'0');
        if (value>(UINT32_MAX-digit)/10u) return -1;
        value=value*10u+digit;
    } while (*++field);
    *result=value;
    return 0;
}

static int scaled_field(const char *field,int32_t *result)
{
    const char *p=field;
    unsigned digits=0;
    if (*p=='+' || *p=='-') ++p;
    while (*p>='0' && *p<='9') {++p;++digits;}
    if (*p=='.') {
        ++p;
        while (*p>='0' && *p<='9') {++p;++digits;}
    }
    if (!digits) return -1;
    if (*p=='e' || *p=='E') {
        ++p;
        if (*p=='+' || *p=='-') ++p;
        if (*p<'0' || *p>'9') return -1;
        do {++p;} while (*p>='0' && *p<='9');
    }
    if (*p) return -1;
    char *end;
    double value=strtod(field,&end)*1000.0;
    if (*end || !isfinite(value) || value<(double)INT32_MIN-0.5 ||
        value>(double)INT32_MAX+0.5) return -1;
    int64_t rounded=(int64_t)(value<0.0?value-0.5:value+0.5);
    if (rounded<INT32_MIN || rounded>INT32_MAX) return -1;
    *result=(int32_t)rounded;
    return 0;
}

static int reject(int result)
{
    ++g_demo.rejected;
    g_demo.last_result=result;
    return result;
}

static int same_sample(const BMS_DemoStatus *sample)
{
    if (sample->source_ms!=g_demo.source_ms || sample->emergency!=g_demo.emergency ||
        sample->current_mA!=g_demo.current_mA || sample->stack_mV!=g_demo.stack_mV ||
        sample->pack_mV!=g_demo.pack_mV || sample->ld_mV!=g_demo.ld_mV) return 0;
    for (unsigned i=0;i<4u;++i)
        if (sample->cell_mV[i]!=g_demo.cell_mV[i] ||
            sample->temp_mC[i]!=g_demo.temp_mC[i]) return 0;
    return 1;
}

int BMS_DemoReceive(const char *csv)
{
    char buffer[256],*fields[15];
    size_t length=0;
    unsigned count=1;
    if (!g_demo.started) return reject(-40);
    if (!csv) return reject(-1);
    while (length<sizeof buffer && csv[length]) ++length;
    if (!length || length>=sizeof buffer) return reject(-1);
    memcpy(buffer,csv,length+1u);
    if (buffer[length-1u]=='\n') {
        buffer[--length]=0;
        if (length && buffer[length-1u]=='\r') buffer[--length]=0;
    }
    fields[0]=buffer;
    for (char *p=buffer;*p;++p) {
        if (*p=='\n' || *p=='\r') return reject(-1);
        if (*p==',') {
            if (count>=15u) return reject(-1);
            *p=0;fields[count++]=p+1;
        }
    }
    if (count!=15u) return reject(-1);
    BMS_DemoStatus next=g_demo;
    if (unsigned_field(fields[0],&next.last_sequence) ||
        unsigned_field(fields[1],&next.source_ms) ||
        unsigned_field(fields[2],&next.emergency) || next.emergency>2u)
        return reject(-1);
    for (unsigned i=0;i<4u;++i)
        if (scaled_field(fields[i+3u],&next.cell_mV[i]) ||
            scaled_field(fields[i+7u],&next.temp_mC[i])) return reject(-1);
    if (scaled_field(fields[11],&next.current_mA) ||
        scaled_field(fields[12],&next.stack_mV) ||
        scaled_field(fields[13],&next.pack_mV) ||
        scaled_field(fields[14],&next.ld_mV)) return reject(-1);
    if (g_demo.active) {
        uint32_t delta=next.last_sequence-g_demo.last_sequence;
        if (!delta) {
            if (!same_sample(&next)) return reject(-43);
            ++g_demo.duplicates;g_demo.last_result=1;
            return 1;
        }
        if (delta>INT32_MAX) return reject(-43);
    }
    int gap=g_demo.active && next.last_sequence-g_demo.last_sequence!=1u;
    next.active=1u;next.algorithm_available=1u;
    ++next.accepted;
    next.last_rx_tick=HAL_GetTick();
    next.last_result=0;
    next.decision_result=0;
    if (gap) DemoJudgeReset();
    if (next.emergency==2u) {
        uint32_t bits=DemoJudge(&next);
        if (bits && !next.fault_bits) next.first_fault_ms=next.source_ms;
        next.fault_bits|=bits;
        next.fault=next.fault_bits!=0u;
    } else {
        next.fault=next.emergency==0u;
        next.fault_bits=0u;
    }
    g_demo=next;
    return 0;
}
