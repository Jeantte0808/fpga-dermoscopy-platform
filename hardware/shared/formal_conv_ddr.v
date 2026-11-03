`timescale 1ns/1ps
// One convolution transaction: DDR parameters -> actual quant bank -> dense/DW
// computation -> successful DDR output responses. No core reset between layers.
// Failure locks the current AXI owner until a coordinated system/bus reset.
module formal_conv_ddr #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,input cfg_depthwise,
 input [15:0] cfg_h,cfg_w,cfg_ci,cfg_co,input [1:0] cfg_k,cfg_s,cfg_p,
 input [29:0] cfg_activation,cfg_weight,cfg_bias,cfg_output,
 input [29:0] cfg_m,cfg_shift,cfg_min,cfg_max,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output reg [1:0] active_owner,output reg read_pending,write_pending,
 output [31:0] parameter_bytes,parameters_sent,read_bytes,write_bytes,valid_scalars
);
 localparam IDLE=0,GEOM=1,CHECK=2,LOAD_START=3,LOAD_WAIT=4,
            HANDOFF=5,LAUNCH=6,RUN=7,DRAIN=8,FINISH=9,FAULT=10,SIZE=11,RANGE=12;
 reg [3:0] state;
 reg selected;reg [15:0] h,w,ci,co,oh,ow,cp;
 reg [1:0] k,s,p;reg [29:0] ab,wb,bb,ob,mb,sb,lb,hb;
 reg [31:0] output_size,wait_cycles;reg [16:0] output_pixels;
 wire lr,ld,lf;wire [7:0] le;wire [29:0] lra;wire [7:0] lrl;
 wire lav,lrr,pc,pv,pr,commit;wire [31:0] pm;wire [7:0] ps,pl,ph;wire [15:0] channels;
 wire [1:0] er,ed,ef,epr,configured,pe;
 wire [7:0] ec[0:1];wire [29:0] wa[0:1],ra[0:1];wire [7:0] wl[0:1],rl[0:1];
 wire [1:0] wav,wv,wla,br,rav,rr;wire [127:0] wd[0:1];
 wire [31:0] reads[0:1],writes[0:1],scalars[0:1];
 assign cmd_ready=state==IDLE&&!failed&&!read_pending&&!write_pending;
 assign pr=epr[selected];
 assign araddr=active_owner==0?lra:ra[selected];
 assign arlen=active_owner==0?lrl:rl[selected];
 assign arvalid=active_owner==0?lav:rav[selected];
 assign rready=active_owner==0?lrr:rr[selected];
 assign awaddr=wa[selected];assign awlen=wl[selected];
 assign awvalid=active_owner!=0&&wav[selected];
 assign wdata=wd[selected];assign wlast=wla[selected];assign wvalid=active_owner!=0&&wv[selected];
 assign bready=active_owner!=0&&br[selected];
 assign read_bytes=parameter_bytes+reads[selected];
 assign write_bytes=writes[selected];assign valid_scalars=scalars[selected];
 formal_quant_loader #(.WATCHDOG(WATCHDOG)) loader(
 .clk(clk),.reset(reset),.cmd_valid(state==LOAD_START),.cmd_ready(lr),.cfg_channels(co),
 .cfg_m(mb),.cfg_s(sb),.cfg_min(lb),.cfg_max(hb),
 .param_clear(pc),.param_valid(pv),.param_ready(pr),.param_m(pm),.param_s(ps),.param_min(pl),.param_max(ph),
 .param_commit(commit),.param_channels(channels),.param_configured(configured[selected]),.param_error(pe[selected]),
 .araddr(lra),.arlen(lrl),.arvalid(lav),.arready(arready&&active_owner==0),
 .rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&active_owner==0),.rready(lrr),
 .done(ld),.failed(lf),.error_code(le),.read_bytes(parameter_bytes),.parameters_sent(parameters_sent));
 formal_joint_ddr_layer #(.WATCHDOG(WATCHDOG)) dense(
 .clk(clk),.reset(reset),.cmd_valid(state==LAUNCH&&!selected),.cmd_ready(er[0]),
 .param_clear(pc&&!selected),.param_valid(pv&&!selected),.param_ready(epr[0]),
 .param_multiplier(pm),.param_shift(ps),.param_min(pl),.param_max(ph),.param_commit(commit&&!selected),
 .param_channels(channels),.param_configured(configured[0]),.param_error(pe[0]),
 .cfg_h(h),.cfg_w(w),.cfg_ci(ci),.cfg_co(co),.cfg_k(k),.cfg_s(s),.cfg_p(p),
 .cfg_activation(ab),.cfg_weight(wb),.cfg_bias(bb),.cfg_output(ob),
 .awaddr(wa[0]),.awlen(wl[0]),.awvalid(wav[0]),.awready(awready&&active_owner==1),
 .wdata(wd[0]),.wlast(wla[0]),.wvalid(wv[0]),.wready(wready&&active_owner==1),.bresp(bresp),.bvalid(bvalid&&active_owner==1),.bready(br[0]),
 .araddr(ra[0]),.arlen(rl[0]),.arvalid(rav[0]),.arready(arready&&active_owner==1),
 .rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&active_owner==1),.rready(rr[0]),
 .done(ed[0]),.failed(ef[0]),.error_code(ec[0]),.read_bytes(reads[0]),.write_bytes(writes[0]),.valid_scalars(scalars[0]));
 formal_dw_ddr_layer #(.WATCHDOG(WATCHDOG)) depthwise(
 .clk(clk),.reset(reset),.cmd_valid(state==LAUNCH&&selected),.cmd_ready(er[1]),
 .param_clear(pc&&selected),.param_valid(pv&&selected),.param_ready(epr[1]),
 .param_multiplier(pm),.param_shift(ps),.param_min(pl),.param_max(ph),.param_commit(commit&&selected),
 .param_channels(channels),.param_configured(configured[1]),.param_error(pe[1]),
 .cfg_h(h),.cfg_w(w),.cfg_ci(ci),.cfg_co(co),.cfg_k(k),.cfg_s(s),.cfg_p(p),
 .cfg_activation(ab),.cfg_weight(wb),.cfg_bias(bb),.cfg_output(ob),
 .awaddr(wa[1]),.awlen(wl[1]),.awvalid(wav[1]),.awready(awready&&active_owner==2),
 .wdata(wd[1]),.wlast(wla[1]),.wvalid(wv[1]),.wready(wready&&active_owner==2),.bresp(bresp),.bvalid(bvalid&&active_owner==2),.bready(br[1]),
 .araddr(ra[1]),.arlen(rl[1]),.arvalid(rav[1]),.arready(arready&&active_owner==2),
 .rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid&&active_owner==2),.rready(rr[1]),
 .done(ed[1]),.failed(ef[1]),.error_code(ec[1]),.read_bytes(reads[1]),.write_bytes(writes[1]),.valid_scalars(scalars[1]));
 wire [30:0] output_end={1'b0,ob}+output_size;
 wire param_overlap=(ob<({1'b0,mb}+({15'd0,cp}<<2))&&mb<output_end)||
                   (ob<({1'b0,sb}+cp)&&sb<output_end)||
                   (ob<({1'b0,lb}+cp)&&lb<output_end)||
                   (ob<({1'b0,hb}+cp)&&hb<output_end);
 task fault;input [7:0] code;begin failed<=1;done<=1;error_code<=code;state<=FAULT;end endtask
 always @(posedge clk)begin
 if(reset)begin
 state<=IDLE;selected<=0;active_owner<=0;h<=0;w<=0;ci<=0;co<=0;oh<=0;ow<=0;cp<=0;k<=0;s<=0;p<=0;
 ab<=0;wb<=0;bb<=0;ob<=0;mb<=0;sb<=0;lb<=0;hb<=0;output_size<=0;output_pixels<=0;wait_cycles<=0;
 done<=0;failed<=0;error_code<=0;read_pending<=0;write_pending<=0;
 end else begin
 done<=0;
 if(arvalid&&arready)read_pending<=1;
 if(rvalid&&rready&&rlast)read_pending<=0;
 if(awvalid&&awready)write_pending<=1;
 if(bvalid&&bready)write_pending<=0;
 if(state==HANDOFF||state==LAUNCH||state==DRAIN)wait_cycles<=wait_cycles+1;else wait_cycles<=0;
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 selected<=cfg_depthwise;active_owner<=0;h<=cfg_h;w<=cfg_w;ci<=cfg_ci;co<=cfg_co;
 k<=cfg_k;s<=cfg_s;p<=cfg_p;ab<=cfg_activation;wb<=cfg_weight;bb<=cfg_bias;ob<=cfg_output;
 mb<=cfg_m;sb<=cfg_shift;lb<=cfg_min;hb<=cfg_max;error_code<=0;state<=GEOM;
 end
 GEOM:begin oh<=((h+2*p-k)>>(s==2?1:0))+1;ow<=((w+2*p-k)>>(s==2?1:0))+1;cp<=((co+15)>>4)<<4;state<=CHECK;end
 CHECK:begin output_pixels<=oh[8:0]*ow[8:0];
 if(h==0||w==0||h>256||w>256||ci==0||ci>960||co==0||co>960||
 (selected&&ci!=co)||(k!=1&&k!=3)||(s!=1&&s!=2)||p>1||h+2*p<k||w+2*p<k)fault(6);
 else state<=SIZE;end
 SIZE:begin output_size<=output_pixels*cp[9:0];state<=RANGE;end
 RANGE:if(param_overlap)fault(6);else state<=LOAD_START;
 LOAD_START:if(lr)state<=LOAD_WAIT;
 LOAD_WAIT:if(ld)begin if(lf)fault(le);else state<=HANDOFF;end
 HANDOFF:if(!read_pending&&!write_pending&&!lav&&!lrr)begin active_owner<=selected?2:1;state<=LAUNCH;end
 LAUNCH:if(er[selected])state<=RUN;
 RUN:if(ed[selected])begin if(ef[selected])fault(ec[selected]);else state<=DRAIN;end
 DRAIN:if(!read_pending&&!write_pending&&!arvalid&&!awvalid&&!wvalid)state<=FINISH;
 FINISH:begin done<=1;state<=IDLE;end
 FAULT:begin end
 default:fault(15);
 endcase
 if(state!=IDLE&&state!=FAULT&&wait_cycles>=WATCHDOG)fault(5);
 end end
endmodule
