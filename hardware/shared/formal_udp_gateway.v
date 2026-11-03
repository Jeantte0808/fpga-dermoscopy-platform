`timescale 1ns/1ps
module formal_udp_gateway #(parameter [47:0] LOCAL_MAC=48'h000a3501fec0,
 parameter [31:0] LOCAL_IP=32'hc0a80002,parameter [15:0] LOCAL_PORT=5000)(
 input net_clk,ui_clk,reset,input gmii_dv,gmii_er,input [7:0] gmii_data,
 output gmii_txen,output [7:0] gmii_txdata,
 input [47:0] peer_mac,input [31:0] peer_ip,input [15:0] peer_port,
 output rx_meta_valid,input rx_meta_ready,output [47:0] rx_mac,output [31:0] rx_ip,output [15:0] rx_port,rx_length,
 output rx_data_valid,input rx_data_ready,output [127:0] rx_data,output [15:0] rx_keep,output rx_last,
 input tx_meta_valid,output tx_meta_ready,input [15:0] tx_length,
 input tx_data_valid,output tx_data_ready,input [127:0] tx_data,input tx_last,
 output failed_net,failed_ui,output [31:0] rejected,transmitted);
 (* ASYNC_REG="TRUE" *) reg [2:0] net_reset_pipe=7,ui_reset_pipe=7;
 always @(posedge net_clk or posedge reset)if(reset)net_reset_pipe<=7;else net_reset_pipe<={net_reset_pipe[1:0],1'b0};
 always @(posedge ui_clk or posedge reset)if(reset)ui_reset_pipe<=7;else ui_reset_pipe<={ui_reset_pipe[1:0],1'b0};
 reg net_reset=1,ui_reset=1;
 always @(posedge net_clk)net_reset<=net_reset_pipe[2];
 always @(posedge ui_clk)ui_reset<=ui_reset_pipe[2];
 wire rv,rdone,rerr,begin_packet,commit_done,commit_error;wire [7:0] rdata;
 wire [15:0] rlen,rport,clen,cport;wire [47:0] rmac,cmac;wire [31:0] rip,cip;
 eth_udp_rx_gmii receiver(.reset_p(net_reset),.local_mac(LOCAL_MAC),.local_ip(LOCAL_IP),.local_port(LOCAL_PORT),
 .exter_mac(rmac),.exter_ip(rip),.exter_port(rport),.rx_data_length(rlen),.data_overflow_i(1'b0),.payload_valid_o(rv),.payload_dat_o(rdata),
 .one_pkt_done(rdone),.pkt_error(rerr),.gmii_rx_clk(net_clk),.gmii_rxdv(gmii_dv),.gmii_rxd(gmii_data));
 formal_rx_commit checker(.clk(net_clk),.reset(net_reset),.gmii_dv(gmii_dv),.gmii_er(gmii_er),.packet_done(rdone),.packet_error(rerr),
 .packet_length(rlen),.packet_mac(rmac),.packet_ip(rip),.packet_port(rport),.packet_start(begin_packet),
 .commit_done(commit_done),.commit_error(commit_error),.commit_length(clen),.commit_port(cport),.commit_mac(cmac),.commit_ip(cip),.rejected(rejected));
 wire rx_sf,rx_df;
 validated_packet_cdc #(.MAX_BYTES(1120)) rx_bridge(.s_clk(net_clk),.d_clk(ui_clk),.reset(reset),
 .rx_start(begin_packet),.rx_valid(rv),.rx_data(rdata),.rx_done(commit_done),.rx_error(commit_error&&commit_done),.rx_length(clen),.rx_mac(cmac),.rx_ip(cip),.rx_port(cport),
 .s_failed(rx_sf),.meta_valid(rx_meta_valid),.meta_ready(rx_meta_ready),.meta_mac(rx_mac),.meta_ip(rx_ip),.meta_port(rx_port),.meta_length(rx_length),
 .data_valid(rx_data_valid),.data_ready(rx_data_ready),.data(rx_data),.keep(rx_keep),.data_last(rx_last),.d_failed(rx_df));
 wire tsr,tstart,tvalid,tdone,tb_fail,tx_sf,tx_df,store_fail;wire [7:0] tbyte;wire [15:0] tlen;
 formal_tx_bytes serialize(.clk(ui_clk),.reset(ui_reset),.meta_valid(tx_meta_valid),.meta_ready(tx_meta_ready),.meta_length(tx_length),
 .data_valid(tx_data_valid),.data_ready(tx_data_ready),.data(tx_data),.data_last(tx_last),
 .capture_ready(tsr),.capture_start(tstart),.capture_valid(tvalid),.capture_data(tbyte),.capture_done(tdone),.length(tlen),.failed(tb_fail));
 wire mv,mr,dv,dr,last;wire [47:0] mac;wire [31:0] ip;wire [15:0] port,len,keep;wire [127:0] data;
 validated_packet_cdc #(.MAX_BYTES(1120)) tx_bridge(.s_clk(ui_clk),.d_clk(net_clk),.reset(reset),
 .rx_start(tstart),.rx_valid(tvalid),.rx_data(tbyte),.rx_done(tdone),.rx_error(1'b0),.rx_length(tlen),.rx_mac(peer_mac),.rx_ip(peer_ip),.rx_port(peer_port),
 .s_ready(tsr),.s_failed(tx_sf),.meta_valid(mv),.meta_ready(mr),.meta_mac(mac),.meta_ip(ip),.meta_port(port),.meta_length(len),
 .data_valid(dv),.data_ready(dr),.data(data),.keep(keep),.data_last(last),.d_failed(tx_df));
 wire start,req,done;wire [7:0] byte_data;wire [47:0] dst_mac;wire [31:0] dst_ip;wire [15:0] dst_port,length;
 formal_tx_packet_store store(.clk(net_clk),.reset(net_reset),.meta_valid(mv),.meta_ready(mr),.meta_length(len),.meta_port(port),.meta_mac(mac),.meta_ip(ip),
 .data_valid(dv),.data_ready(dr),.data(data),.keep(keep),.data_last(last),.tx_start(start),.tx_req(req),.tx_done(done),.tx_byte(byte_data),
 .tx_length(length),.tx_port(dst_port),.tx_mac(dst_mac),.tx_ip(dst_ip),.failed(store_fail),.transmitted(transmitted));
 eth_udp_tx_gmii sender(.clk125m(net_clk),.reset_p(net_reset),.tx_en_pulse(start),.tx_done(done),.dst_mac(dst_mac),.src_mac(LOCAL_MAC),.dst_ip(dst_ip),.src_ip(LOCAL_IP),
 .dst_port(dst_port),.src_port(LOCAL_PORT),.data_length(length),.payload_req_o(req),.payload_dat_i(byte_data),.gmii_txen(gmii_txen),.gmii_txd(gmii_txdata));
 assign failed_net=rx_sf||tx_df||store_fail;assign failed_ui=rx_df||tx_sf||tb_fail;
endmodule
