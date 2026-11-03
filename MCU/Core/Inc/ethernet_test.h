#ifndef ETHERNET_TEST_H
#define ETHERNET_TEST_H
#include <stdint.h>
#define ETH_TEST_PACKET_SIZE 256u

typedef struct {
    uint32_t ready,link_up,pending,sequence,tx_count,error_count,last_tx_tick;
    int32_t last_result;
    uint8_t version,phy,socket_status;
    uint32_t rx_count,rx_rejected,last_ai_tick;
    int32_t last_rx_result;
    uint8_t ai_rx_received;
} Ethernet_Status;
extern volatile Ethernet_Status g_eth;
void Ethernet_Init(void);
void Ethernet_Update(void);

uint16_t Ethernet_Encode(uint8_t packet[ETH_TEST_PACKET_SIZE],uint32_t sequence);
#endif
