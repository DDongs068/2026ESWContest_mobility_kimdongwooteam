#ifndef ETHERNET_TEST_H
#define ETHERNET_TEST_H
#include <stdint.h>
#define ETH_TEST_PACKET_SIZE 384u /* Maximum CSV buffer, not fixed payload size. */
typedef struct {
    uint32_t enable;       /* 0=TX off, 1=periodic UDP TX. Boot default0. */
    uint32_t send_once;    /* 1=one packet, consumed when attempted. */
    uint32_t reinit;       /* 1=reset W5500/apply network config, no motor change. */
    uint32_t period_ms;    /* Default100, effective minimum100. */
    uint8_t ip[4], peer_ip[4], subnet[4], gateway[4], mac[6];
    uint16_t local_port, peer_port; /* Network edits require reinit=1. */
    uint32_t motor_rx_enable; /* Default1: standalone Pi motor commands on motor_port. */
    uint16_t motor_port;       /* Default5002; network edits require reinit=1. */
    uint32_t telemetry_v2; /* Default1: tagged BMS2 with balancing and live protection. */
    uint32_t ai_rx_enable; /* Default1: accept AI:0 / AI:1 from Pi at peer_ip. */
} Ethernet_Control;
typedef struct {
    uint32_t ready, link_up, pending, sequence;
    uint32_t tx_count, error_count, last_tx_tick;
    int32_t last_result; /* 0=OK, 1=pending, -1=SPI, -2=VERSION,
                           -3=socket, -4=link, -5=timeout, -6=TX free,
                           -7=config readback, -8=RX header, -9=CSV encoding.
                           SEND_OK is not PC receipt. */
    uint8_t version, phy, socket_status;
    uint32_t rx_count, rx_rejected, last_ai_tick;
    int32_t last_rx_result; /* 0=accepted (or no packet yet), 1=format,
                              2=wrong peer IP, 3=RX disabled; negative=I/O. */
    uint8_t ai_rx_received; /* At least one valid AI packet since MCU boot. */
} Ethernet_Status;
extern volatile Ethernet_Control g_eth_control;
extern volatile Ethernet_Status g_eth;
void Ethernet_Test_Init(void);
void Ethernet_Test_Update(void);
/* One ASCII CSV line, LF terminated. Returns payload bytes, excluding NUL.
 * seq,ms,valid,V1,V2,V3,V4,T1,T2,T3,T4,I,STACK,PACK,LD
 * Units: volts, degrees C, amps; invalid samples contain NA values. */
uint16_t Ethernet_Test_Encode(uint8_t packet[ETH_TEST_PACKET_SIZE], uint32_t sequence);
#endif
