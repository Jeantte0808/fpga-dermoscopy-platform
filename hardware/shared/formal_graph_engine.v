`timescale 1ns/1ps
// model_verified must come from the future CRC/model/session preflight manager.
// No Ethernet START or network upload is implemented in this component.
module formal_graph_engine #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,
 input model_verified,input [31:0] model_generation,frame_id,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output busy,done,failed,result_valid,axi_owned,output [7:0] error_code,
 output [6:0] stage_index,output [31:0] completed_stages,active_frame_id,active_generation
);
 wire [1023:0] d;wire [2:0] owner;wire ev;
 wire [4:0] ready,ed,ef;wire [7:0] ec[0:4];
 wire [31:0] writes[0:4],counts[0:4];
 wire [29:0] aa[0:4],ra[0:4];wire [7:0] al[0:4],rl[0:4];
 wire [4:0] av,wv,wl,br,rv,rr;wire [127:0] wd[0:4];
 reg read_pending,write_pending;
 assign axi_owned=busy||failed;
 assign awaddr=aa[owner];assign awlen=al[owner];assign awvalid=av[owner];
 assign wdata=wd[owner];assign wvalid=wv[owner];assign wlast=wl[owner];assign bready=br[owner];
 assign araddr=ra[owner];assign arlen=rl[owner];assign arvalid=rv[owner];assign rready=rr[owner];
 always @(posedge clk)if(reset)begin read_pending<=0;write_pending<=0;end else begin
 if(arvalid&&arready)read_pending<=1;if(rvalid&&rready&&rlast)read_pending<=0;
 if(awvalid&&awready)write_pending<=1;if(bvalid&&bready)write_pending<=0;end
 formal_graph_control #(.WATCHDOG(WATCHDOG)) controller(
 .clk(clk),.reset(reset),.cmd_valid(cmd_valid),.cmd_ready(cmd_ready),.model_verified(model_verified),.model_generation(model_generation),.frame_id(frame_id),
 .execute_valid(ev),.execute_ready(ready[owner]),.execute_done(ed[owner]),.execute_failed(ef[owner]),.execute_error(ec[owner]),
 .execute_write_bytes(writes[owner]),.execute_scalars(counts[owner]),.read_pending(read_pending),.write_pending(write_pending),
 .descriptor(d),.owner(owner),.stage_index(stage_index),.completed_stages(completed_stages),
 .active_frame_id(active_frame_id),.active_generation(active_generation),.busy(busy),.done(done),.failed(failed),.result_valid(result_valid),.error_code(error_code));
 wire [31:0] f_op=d[0*32+:32];
 wire [31:0] f_h=d[1*32+:32];
 wire [31:0] f_w=d[2*32+:32];
 wire [31:0] f_ci=d[3*32+:32];
 wire [31:0] f_co=d[4*32+:32];
 wire [31:0] f_oh=d[5*32+:32];
 wire [31:0] f_ow=d[6*32+:32];
 wire [31:0] f_k=d[7*32+:32];
 wire [31:0] f_s=d[8*32+:32];
 wire [31:0] f_p=d[9*32+:32];
 wire [31:0] f_activation=d[10*32+:32];
 wire [31:0] f_second=d[11*32+:32];
 wire [31:0] f_bh=d[12*32+:32];
 wire [31:0] f_bw=d[13*32+:32];
 wire [31:0] f_cb=d[14*32+:32];
 wire [31:0] f_output=d[15*32+:32];
 wire [31:0] f_weight=d[16*32+:32];
 wire [31:0] f_bias=d[17*32+:32];
 wire [31:0] f_m=d[18*32+:32];
 wire [31:0] f_shift=d[19*32+:32];
 wire [31:0] f_minimum=d[20*32+:32];
 wire [31:0] f_maximum=d[21*32+:32];
 wire [31:0] f_ma=d[22*32+:32];
 wire [31:0] f_mb=d[23*32+:32];
 wire [31:0] f_sa=d[24*32+:32];
 wire [31:0] f_sb=d[25*32+:32];
 wire [31:0] f_bpa=d[26*32+:32];
 wire [31:0] f_bpb=d[27*32+:32];
 wire [31:0] f_expected_write_bytes=d[28*32+:32];
 wire [31:0] f_expected_scalars=d[29*32+:32];
 wire [31:0] f_rgb=d[30*32+:32];
 wire [31:0] f_lut=d[31*32+:32];
 formal_rgb_ddr #(.WATCHDOG(WATCHDOG)) rgb(
 .clk(clk),
 .reset(reset),
 .cmd_valid(ev&&owner==0),
 .cmd_ready(ready[0]),
 .done(ed[0]),
 .failed(ef[0]),
 .error_code(ec[0]),
 .write_bytes(writes[0]),
 .awaddr(aa[0]),
 .awlen(al[0]),
 .awvalid(av[0]),
 .awready(awready&&owner==0),
 .wdata(wd[0]),
 .wlast(wl[0]),
 .wvalid(wv[0]),
 .wready(wready&&owner==0),
 .bresp(bresp),
 .bvalid(bvalid&&owner==0),
 .bready(br[0]),
 .araddr(ra[0]),
 .arlen(rl[0]),
 .arvalid(rv[0]),
 .arready(arready&&owner==0),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid&&owner==0),
 .rready(rr[0]),
 .cfg_rgb(f_rgb[29:0]),
 .cfg_lut(f_lut[29:0]),
 .cfg_output(f_output[29:0]),
 .committed_pixels(counts[0]));
 formal_conv_ddr #(.WATCHDOG(WATCHDOG)) conv(
 .clk(clk),
 .reset(reset),
 .cmd_valid(ev&&owner==1),
 .cmd_ready(ready[1]),
 .done(ed[1]),
 .failed(ef[1]),
 .error_code(ec[1]),
 .write_bytes(writes[1]),
 .awaddr(aa[1]),
 .awlen(al[1]),
 .awvalid(av[1]),
 .awready(awready&&owner==1),
 .wdata(wd[1]),
 .wlast(wl[1]),
 .wvalid(wv[1]),
 .wready(wready&&owner==1),
 .bresp(bresp),
 .bvalid(bvalid&&owner==1),
 .bready(br[1]),
 .araddr(ra[1]),
 .arlen(rl[1]),
 .arvalid(rv[1]),
 .arready(arready&&owner==1),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid&&owner==1),
 .rready(rr[1]),
 .cfg_h(f_h[15:0]),
 .cfg_w(f_w[15:0]),
 .cfg_ci(f_ci[15:0]),
 .cfg_co(f_co[15:0]),
 .cfg_k(f_k[1:0]),
 .cfg_s(f_s[1:0]),
 .cfg_p(f_p[1:0]),
 .cfg_activation(f_activation[29:0]),
 .cfg_weight(f_weight[29:0]),
 .cfg_bias(f_bias[29:0]),
 .cfg_output(f_output[29:0]),
 .cfg_m(f_m[29:0]),
 .cfg_shift(f_shift[29:0]),
 .cfg_min(f_minimum[29:0]),
 .cfg_max(f_maximum[29:0]),
 .cfg_depthwise(f_op==1),
 .valid_scalars(counts[1]));
 formal_residual_ddr #(.WATCHDOG(WATCHDOG)) residual(
 .clk(clk),
 .reset(reset),
 .cmd_valid(ev&&owner==2),
 .cmd_ready(ready[2]),
 .done(ed[2]),
 .failed(ef[2]),
 .error_code(ec[2]),
 .write_bytes(writes[2]),
 .awaddr(aa[2]),
 .awlen(al[2]),
 .awvalid(av[2]),
 .awready(awready&&owner==2),
 .wdata(wd[2]),
 .wlast(wl[2]),
 .wvalid(wv[2]),
 .wready(wready&&owner==2),
 .bresp(bresp),
 .bvalid(bvalid&&owner==2),
 .bready(br[2]),
 .araddr(ra[2]),
 .arlen(rl[2]),
 .arvalid(rv[2]),
 .arready(arready&&owner==2),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid&&owner==2),
 .rready(rr[2]),
 .cfg_h(f_h[15:0]),
 .cfg_w(f_w[15:0]),
 .cfg_ca(f_ci[15:0]),
 .cfg_bh(f_bh[15:0]),
 .cfg_bw(f_bw[15:0]),
 .cfg_cb(f_cb[15:0]),
 .cfg_oh(f_oh[15:0]),
 .cfg_ow(f_ow[15:0]),
 .cfg_co(f_co[15:0]),
 .cfg_a(f_activation[29:0]),
 .cfg_b(f_second[29:0]),
 .cfg_output(f_output[29:0]),
 .cfg_op(f_op[2:0]),
 .cfg_ma(f_ma),
 .cfg_mb(f_mb),
 .cfg_sa(f_sa[7:0]),
 .cfg_sb(f_sb[7:0]),
 .cfg_bpa(f_bpa[0]),
 .cfg_bpb(f_bpb[0]),
 .valid_scalars(counts[2]));
 formal_decode_ops #(.WATCHDOG(WATCHDOG)) decode(
 .clk(clk),
 .reset(reset),
 .cmd_valid(ev&&owner==3),
 .cmd_ready(ready[3]),
 .done(ed[3]),
 .failed(ef[3]),
 .error_code(ec[3]),
 .write_bytes(writes[3]),
 .awaddr(aa[3]),
 .awlen(al[3]),
 .awvalid(av[3]),
 .awready(awready&&owner==3),
 .wdata(wd[3]),
 .wlast(wl[3]),
 .wvalid(wv[3]),
 .wready(wready&&owner==3),
 .bresp(bresp),
 .bvalid(bvalid&&owner==3),
 .bready(br[3]),
 .araddr(ra[3]),
 .arlen(rl[3]),
 .arvalid(rv[3]),
 .arready(arready&&owner==3),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid&&owner==3),
 .rready(rr[3]),
 .cfg_h(f_h[15:0]),
 .cfg_w(f_w[15:0]),
 .cfg_ca(f_ci[15:0]),
 .cfg_bh(f_bh[15:0]),
 .cfg_bw(f_bw[15:0]),
 .cfg_cb(f_cb[15:0]),
 .cfg_oh(f_oh[15:0]),
 .cfg_ow(f_ow[15:0]),
 .cfg_co(f_co[15:0]),
 .cfg_a(f_activation[29:0]),
 .cfg_b(f_second[29:0]),
 .cfg_output(f_output[29:0]),
 .cfg_op(f_op[2:0]),
 .cfg_ma(f_ma),
 .cfg_mb(f_mb),
 .cfg_sa(f_sa[7:0]),
 .cfg_sb(f_sb[7:0]),
 .cfg_bpa(f_bpa[0]),
 .cfg_bpb(f_bpb[0]),
 .valid_scalars(counts[3]));
 wire [31:0] mask_words;assign counts[4]=mask_words<<4;
 formal_mask_ddr #(.WATCHDOG(WATCHDOG)) mask(
 .clk(clk),
 .reset(reset),
 .cmd_valid(ev&&owner==4),
 .cmd_ready(ready[4]),
 .done(ed[4]),
 .failed(ef[4]),
 .error_code(ec[4]),
 .write_bytes(writes[4]),
 .awaddr(aa[4]),
 .awlen(al[4]),
 .awvalid(av[4]),
 .awready(awready&&owner==4),
 .wdata(wd[4]),
 .wlast(wl[4]),
 .wvalid(wv[4]),
 .wready(wready&&owner==4),
 .bresp(bresp),
 .bvalid(bvalid&&owner==4),
 .bready(br[4]),
 .araddr(ra[4]),
 .arlen(rl[4]),
 .arvalid(rv[4]),
 .arready(arready&&owner==4),
 .rdata(rdata),
 .rresp(rresp),
 .rlast(rlast),
 .rvalid(rvalid&&owner==4),
 .rready(rr[4]),
 .cfg_source(f_activation[29:0]),
 .cfg_output(f_output[29:0]),
 .committed_words(mask_words));
endmodule
