`timescale 1ns/1ps
// Actual GMII and both actual XPM packet bridges around the actual model core.
// MIG is attached by formal_ethernet_top, not simulated by the control fixture.
module formal_ethernet_session #(parameter WATCHDOG=100000)(
 input net_clk,ui_clk,reset,input gmii_dv,gmii_er,input [7:0] gmii_data,output gmii_txen,output [7:0] gmii_txdata,
 input [47:0] peer_mac,input [31:0] peer_ip,input [15:0] peer_port,
 output failed,output [7:0] error_code,output [31:0] generation,
 output model_verified,result_valid,frame_locked,output [31:0] result_frame_id,result_generation,cache_replays,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready);
 wire mv,mr,dv,dr,last,tm,tr,tv,tready,tl,gfn,gfu,nf;wire [47:0] mac;wire [31:0] ip;wire [15:0] port,len,keep,txlen;wire [127:0] data,txdata;wire [7:0] ne;
 (* ASYNC_REG="TRUE" *) reg [2:0] ui_reset_pipe=7;
 always @(posedge ui_clk or posedge reset)if(reset)ui_reset_pipe<=7;else ui_reset_pipe<={ui_reset_pipe[1:0],1'b0};
 reg ui_reset=1;
 always @(posedge ui_clk)ui_reset<=ui_reset_pipe[2];
 (* ASYNC_REG="TRUE" *) reg [1:0] gateway_error_sync;
 always @(posedge ui_clk)if(ui_reset)gateway_error_sync<=0;else gateway_error_sync<={gateway_error_sync[0],gfn};
 wire transport_failed=gfu||gateway_error_sync[1];
 formal_udp_gateway gateway(.net_clk(net_clk),.ui_clk(ui_clk),.reset(reset),.gmii_dv(gmii_dv),.gmii_er(gmii_er),.gmii_data(gmii_data),.gmii_txen(gmii_txen),.gmii_txdata(gmii_txdata),
 .peer_mac(peer_mac),.peer_ip(peer_ip),.peer_port(peer_port),
 .rx_meta_valid(mv),.rx_meta_ready(mr),.rx_mac(mac),.rx_ip(ip),.rx_port(port),.rx_length(len),
 .rx_data_valid(dv),.rx_data_ready(dr),.rx_data(data),.rx_keep(keep),.rx_last(last),
 .tx_meta_valid(tm),.tx_meta_ready(tr),.tx_length(txlen),.tx_data_valid(tv),.tx_data_ready(tready),.tx_data(txdata),.tx_last(tl),.failed_net(gfn),.failed_ui(gfu));
 wire session_mr,session_dr;
 assign mr=session_mr&&!transport_failed;assign dr=session_dr&&!transport_failed;
 assign failed=nf||transport_failed;assign error_code=nf?ne:transport_failed?8'd150:8'd0;
 formal_network_session #(.WATCHDOG(WATCHDOG)) session(
 .clk(ui_clk),.reset(ui_reset),.meta_valid(mv&&!transport_failed),.meta_ready(session_mr),.meta_length(len),.meta_port(port),.meta_mac(mac),.meta_ip(ip),
 .data_valid(dv&&!transport_failed),.data_ready(session_dr),.data(data),.keep(keep),.data_last(last),.peer_mac(peer_mac),.peer_ip(peer_ip),.peer_port(peer_port),
 .tx_meta_valid(tm),.tx_meta_ready(tr),.tx_bytes(txlen),.tx_valid(tv),.tx_ready(tready),.tx_data(txdata),.tx_last(tl),
 .failed(nf),.error_code(ne),.generation(generation),.model_verified(model_verified),.result_valid(result_valid),.frame_locked(frame_locked),.result_frame_id(result_frame_id),.result_generation(result_generation),.cache_replays(cache_replays),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready));
endmodule
