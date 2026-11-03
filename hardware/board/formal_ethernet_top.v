`timescale 1ns/1ps
// MIG port/pin configuration adapted from the vendor chapter 34 200T/32-bit example.
module formal_ethernet_top(input FPGA_CLK,input reset_n,
 input rgmii_rx_clk_i,input [3:0] rgmii_rxd,input rgmii_rxdv,
 output eth_rst_n,output rgmii_tx_clk,output [3:0] rgmii_txd,output rgmii_txen,output [7:0] led,
 inout [31:0] ddr3_dq,inout [3:0] ddr3_dqs_n,ddr3_dqs_p,
 output [14:0] ddr3_addr,output [2:0] ddr3_ba,
 output ddr3_ras_n,ddr3_cas_n,ddr3_we_n,ddr3_reset_n,
 output [0:0] ddr3_ck_p,ddr3_ck_n,ddr3_cke,ddr3_cs_n,ddr3_odt,
 output [3:0] ddr3_dm);
 `include "board_config.vh"
 wire eth_clk,eth_pll_locked;
 clk_wiz_eth eth_pll(.clk_in1(rgmii_rx_clk_i),.clk_out1(eth_clk),.locked(eth_pll_locked));
 assign eth_rst_n=1'b1;
 wire feedback_raw,feedback,ref_raw,ddr3_clk200m,pll_locked;
 MMCME2_BASE #(.CLKIN1_PERIOD(20.0),.CLKFBOUT_MULT_F(20.0),
 .DIVCLK_DIVIDE(1),.CLKOUT0_DIVIDE_F(5.0)) ref_pll(
 .CLKIN1(FPGA_CLK),.CLKFBIN(feedback),.CLKFBOUT(feedback_raw),
 .CLKOUT0(ref_raw),.LOCKED(pll_locked),.RST(!reset_n),.PWRDWN(1'b0));
 BUFG fb_buf(.I(feedback_raw),.O(feedback));
 BUFG ref_buf(.I(ref_raw),.O(ddr3_clk200m));
 reg [15:0] power_delay=0;
 always @(posedge ddr3_clk200m or negedge pll_locked)
 if(!pll_locked) power_delay<=0;else power_delay<={power_delay[14:0],1'b1};
 wire ddr3_rst_n=power_delay[15]&&eth_pll_locked;
  wire          ui_clk;
  wire          ui_clk_sync_rst;
  wire          mmcm_locked;
  wire          init_calib_complete;

  wire [3:0]    s_axi_awid;
  wire [29:0]   s_axi_awaddr;
  wire [7:0]    s_axi_awlen;
  wire [2:0]    s_axi_awsize;
  wire [1:0]    s_axi_awburst;
  wire [0:0]    s_axi_awlock;
  wire [3:0]    s_axi_awcache;
  wire [2:0]    s_axi_awprot;
  wire [3:0]    s_axi_awqos;
  wire [3:0]    s_axi_awregion;
  wire          s_axi_awvalid;
  wire          s_axi_awready;

  wire [127:0]  s_axi_wdata;
  wire [15:0]   s_axi_wstrb;
  wire          s_axi_wlast;
  wire          s_axi_wvalid;
  wire          s_axi_wready;

  wire [3:0]    s_axi_bid;
  wire [1:0]    s_axi_bresp;
  wire          s_axi_bvalid;
  wire          s_axi_bready;

  wire [3:0]    s_axi_arid;
  wire [29:0]   s_axi_araddr;
  wire [7:0]    s_axi_arlen;
  wire [2:0]    s_axi_arsize;
  wire [1:0]    s_axi_arburst;
  wire [0:0]    s_axi_arlock;
  wire [3:0]    s_axi_arcache;
  wire [2:0]    s_axi_arprot;
  wire [3:0]    s_axi_arqos;
  wire [3:0]    s_axi_arregion;
  wire          s_axi_arvalid;
  wire          s_axi_arready;

  wire [3:0]    s_axi_rid;
  wire [127:0]  s_axi_rdata;
  wire [1:0]    s_axi_rresp;
  wire          s_axi_rlast;
  wire          s_axi_rvalid;
  wire          s_axi_rready;


 wire ui_ready=!ui_clk_sync_rst&&init_calib_complete&&eth_pll_locked;
 (* ASYNC_REG="TRUE" *) reg [3:0] axi_reset_pipe=0;
 always @(posedge ui_clk or negedge ui_ready)
 if(!ui_ready)axi_reset_pipe<=0;else axi_reset_pipe<={axi_reset_pipe[2:0],1'b1};
 wire axi_reset_n=axi_reset_pipe[3];
 (* ASYNC_REG="TRUE" *) reg [3:0] io_reset_pipe=0;
 always @(posedge eth_clk or negedge axi_reset_n)
 if(!axi_reset_n)io_reset_pipe<=0;else io_reset_pipe<={io_reset_pipe[2:0],1'b1};
 wire io_reset_n=io_reset_pipe[3];
 assign s_axi_awid=0;assign s_axi_arid=0;
 assign s_axi_awsize=4;assign s_axi_arsize=4;
 assign s_axi_awburst=1;assign s_axi_arburst=1;
 assign s_axi_awlock=0;assign s_axi_arlock=0;
 assign s_axi_awcache=0;assign s_axi_arcache=0;
 assign s_axi_awprot=0;assign s_axi_arprot=0;
 assign s_axi_awqos=0;assign s_axi_arqos=0;
 assign s_axi_wstrb=16'hffff;
 wire gmii_clk,gmii_dv,gmii_er;wire [7:0] gmii_data;
 rgmii_to_gmii phy_rx(.reset_n(io_reset_n),.rgmii_rx_clk(eth_clk),.rgmii_rxd(rgmii_rxd),.rgmii_rxdv(rgmii_rxdv),
 .gmii_rx_clk(gmii_clk),.gmii_rxd(gmii_data),.gmii_rxdv(gmii_dv),.gmii_rxerr(gmii_er));
 wire tx_en;wire [7:0] tx_data,error_code;wire failed,model_verified,result_valid,frame_locked;
 wire [31:0] generation,result_frame,result_generation,replays;
 formal_ethernet_board_session system(.net_clk(gmii_clk),.ui_clk(ui_clk),.reset(!axi_reset_n),
 .gmii_dv(gmii_dv),.gmii_er(gmii_er),.gmii_data(gmii_data),.gmii_txen(tx_en),.gmii_txdata(tx_data),
 .peer_mac(PC_MAC),.peer_ip(PC_IP),.peer_port(PC_PORT),.failed(failed),.error_code(error_code),.generation(generation),
 .model_verified(model_verified),.result_valid(result_valid),.frame_locked(frame_locked),.result_frame_id(result_frame),.result_generation(result_generation),.cache_replays(replays),
 .awaddr(s_axi_awaddr),.awlen(s_axi_awlen),.awvalid(s_axi_awvalid),.awready(s_axi_awready),
 .wdata(s_axi_wdata),.wlast(s_axi_wlast),.wvalid(s_axi_wvalid),.wready(s_axi_wready),.bresp(s_axi_bresp),.bvalid(s_axi_bvalid),.bready(s_axi_bready),
 .araddr(s_axi_araddr),.arlen(s_axi_arlen),.arvalid(s_axi_arvalid),.arready(s_axi_arready),
 .rdata(s_axi_rdata),.rresp(s_axi_rresp),.rlast(s_axi_rlast),.rvalid(s_axi_rvalid),.rready(s_axi_rready));
 gmii_to_rgmii phy_tx(.reset_n(io_reset_n),.gmii_tx_clk(gmii_clk),.gmii_txd(tx_data),.gmii_txen(tx_en),.gmii_txer(1'b0),
 .rgmii_tx_clk(rgmii_tx_clk),.rgmii_txd(rgmii_txd),.rgmii_txen(rgmii_txen));
 reg [25:0] heartbeat;
 always @(posedge ui_clk)if(!axi_reset_n)heartbeat<=0;else heartbeat<=heartbeat+1'b1;
 // Independent diagnostic bits only: not a coherent multi-bit control transfer.
 reg [3:0] status_ui_reg=0;
 always @(posedge ui_clk)if(!axi_reset_n)status_ui_reg<=0;else status_ui_reg<={failed,result_valid,model_verified,frame_locked};
 wire [7:0] status_bits={status_ui_reg,init_calib_complete,eth_pll_locked,pll_locked,heartbeat[25]};
 (* ASYNC_REG="TRUE" *) reg [7:0] led_meta,led_sync;
 always @(posedge FPGA_CLK)begin led_meta<=status_bits;led_sync<=led_meta;end
 assign led=led_sync;
  mig_7series_0 u_mig_7series_0 (
    // Memory interface ports
    .ddr3_addr            (ddr3_addr           ),
    .ddr3_ba              (ddr3_ba             ),
    .ddr3_cas_n           (ddr3_cas_n          ),
    .ddr3_ck_n            (ddr3_ck_n           ),
    .ddr3_ck_p            (ddr3_ck_p           ),
    .ddr3_cke             (ddr3_cke            ),
    .ddr3_ras_n           (ddr3_ras_n          ),
    .ddr3_reset_n         (ddr3_reset_n        ),
    .ddr3_we_n            (ddr3_we_n           ),
    .ddr3_dq              (ddr3_dq             ),
    .ddr3_dqs_n           (ddr3_dqs_n          ),
    .ddr3_dqs_p           (ddr3_dqs_p          ),
    .init_calib_complete  (init_calib_complete ),
    .ddr3_cs_n            (ddr3_cs_n           ),
    .ddr3_dm              (ddr3_dm             ),
    .ddr3_odt             (ddr3_odt            ),
    // Application interface ports
    .ui_clk               (ui_clk              ),
    .ui_clk_sync_rst      (ui_clk_sync_rst     ),
    .mmcm_locked          (mmcm_locked         ),
    .aresetn              (axi_reset_n          ),
    .app_sr_req           (1'b0                ),
    .app_ref_req          (1'b0                ),
    .app_zq_req           (1'b0                ),
    .app_sr_active        (                    ),
    .app_ref_ack          (                    ),
    .app_zq_ack           (                    ),
    // Slave Interface Write Address Ports
    .s_axi_awid           (s_axi_awid          ),
    .s_axi_awaddr         (s_axi_awaddr        ),
    .s_axi_awlen          (s_axi_awlen         ),
    .s_axi_awsize         (s_axi_awsize        ),
    .s_axi_awburst        (s_axi_awburst       ),
    .s_axi_awlock         (s_axi_awlock        ),
    .s_axi_awcache        (s_axi_awcache       ),
    .s_axi_awprot         (s_axi_awprot        ),
    .s_axi_awqos          (s_axi_awqos         ),
    .s_axi_awvalid        (s_axi_awvalid       ),
    .s_axi_awready        (s_axi_awready       ),
    // Slave Interface Write Data Ports
    .s_axi_wdata          (s_axi_wdata         ),
    .s_axi_wstrb          (s_axi_wstrb         ),
    .s_axi_wlast          (s_axi_wlast         ),
    .s_axi_wvalid         (s_axi_wvalid        ),
    .s_axi_wready         (s_axi_wready        ),
    // Slave Interface Write Response Ports
    .s_axi_bid            (s_axi_bid           ),
    .s_axi_bresp          (s_axi_bresp         ),
    .s_axi_bvalid         (s_axi_bvalid        ),
    .s_axi_bready         (s_axi_bready        ),
    // Slave Interface Read Address Ports
    .s_axi_arid           (s_axi_arid          ),
    .s_axi_araddr         (s_axi_araddr        ),
    .s_axi_arlen          (s_axi_arlen         ),
    .s_axi_arsize         (s_axi_arsize        ),
    .s_axi_arburst        (s_axi_arburst       ),
    .s_axi_arlock         (s_axi_arlock        ),
    .s_axi_arcache        (s_axi_arcache       ),
    .s_axi_arprot         (s_axi_arprot        ),
    .s_axi_arqos          (s_axi_arqos         ),
    .s_axi_arvalid        (s_axi_arvalid       ),
    .s_axi_arready        (s_axi_arready       ),
    // Slave Interface Read Data Ports
    .s_axi_rid            (s_axi_rid           ),
    .s_axi_rdata          (s_axi_rdata         ),
    .s_axi_rresp          (s_axi_rresp         ),
    .s_axi_rlast          (s_axi_rlast         ),
    .s_axi_rvalid         (s_axi_rvalid        ),
    .s_axi_rready         (s_axi_rready        ),
    // System Clock Ports
    .sys_clk_i            (ddr3_clk200m        ),
    .sys_rst              (ddr3_rst_n          ) //active low
  );


endmodule
