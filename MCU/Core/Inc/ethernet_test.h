#ifndef ETHERNET_TEST_H
#define ETHERNET_TEST_H
#include <stdint.h>
#define ETH_TEST_PACKET_SIZE 384u
typedef struct {
    uint32_t enable;
    uint32_t send_once;
    uint32_t reinit;
    uint32_t period_ms;
    uint8_t ip[4], peer_ip[4], subnet[4], gateway[4], mac[6];
    uint16_t local_port, peer_port;
    uint32_t motor_rx_enable;
    uint16_t motor_port;
    uint32_t telemetry_v2;
    uint32_t ai_rx_enable;
} Ethernet_Control;
typedef struct {
    uint32_t ready, link_up, pending, sequence;
    uint32_t tx_count, error_count, last_tx_tick;
    int32_t last_result;
    uint8_t version, phy, socket_status;
    uint32_t rx_count, rx_rejected, last_ai_tick;
    int32_t last_rx_result;
    uint8_t ai_rx_received;
} Ethernet_Status;
extern volatile Ethernet_Control g_eth_control;
extern volatile Ethernet_Status g_eth;
void Ethernet_Test_Init(void);
void Ethernet_Test_Update(void);
uint16_t Ethernet_Test_Encode(uint8_t packet[ETH_TEST_PACKET_SIZE], uint32_t sequence);
#endif
