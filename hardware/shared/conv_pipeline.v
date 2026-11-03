`timescale 1ns/1ps
// Shared dense/depthwise tensor pipeline. Local HWC storage is padded to C16.
// Dense weights: O,KY,KX,C16. DW weights: C16-group,KY,KX,16 lanes.
// Four address/response stages can run concurrently with the MAC pipeline.
module conv_pipeline #(parameter DEPTHWISE=0)(
 input clk,reset,input cmd_valid,output cmd_ready,
 input [15:0] cfg_h,cfg_w,cfg_ci,cfg_co,cfg_oh,cfg_ow,
 input [1:0] cfg_kernel,cfg_stride,cfg_pad,
 input [15:0] cfg_activation,cfg_weight,cfg_bias,input cfg_gaps,
 output [11:0] mem_addr,output mem_en,input [127:0] mem_q,
 output out_valid,input out_ready,output [511:0] out_result,
 output [15:0] out_overflow,output [15:0] out_x,out_y,out_channel,
 output reg done,failed,output protocol_error,
 output reg [31:0] chunks,weight_bytes,cache_groups,feed_cycles,max_run,overlap_cycles,
 output accepted_beat
);
 localparam OUT_LANES=DEPTHWISE ? 16:8;
 localparam IDLE=0,VALIDATE=1,CHECK=2,GROUP_INIT=3,WLOAD=4,WDRAIN=5,
 BREQ=6,BTAKE=7,GEN_INIT=8,GEN=9,DRAIN=10;
 reg [3:0] state;
 reg [15:0] h,w,ci,co,oh,ow,oc;
 reg [1:0] kernel,stride,pad;
 reg [11:0] ab,wb,bb,weight_address,group_weight_base;
 reg [5:0] blocks;
 reg [8:0] total_blocks,weight_block,issue_index;
 reg [5:0] input_block;
 reg [2:0] weight_lane;
 reg [1:0] kx,ky,bias_word;
 reg [15:0] ox,oy;
 reg [16:0] pixels,input_pixels;
 reg [31:0] activation_end,weight_end,bias_end;
 reg bad_config,gaps;
 reg [15:0] last_mask;
 reg wp_valid,wp_keep;
 reg [2:0] wp_lane;
 reg [8:0] wp_block;
 reg [1023:0] weight_collect;
 (* ram_style="block" *) reg [1023:0] cache[0:511];
 reg [1023:0] weight_q;
 reg [511:0] biases;
 reg va,vb,vc,req_valid,keep_b,keep_c,response_zero;
 reg signed [17:0] ix_a,iy_a;
 reg signed [16:0] pixel_b;
 reg signed [31:0] word_c;
 reg [5:0] cb_a,cb_b;
 reg [74:0] meta_a,meta_b,meta_c,response_meta;
 reg [47:0] tags[0:3];
 reg [31:0] group_outputs,run_length,lfsr;
 reg [3:0] gap_left;
 wire core_ready;
 assign accepted_beat=req_valid && core_ready;
 wire issue=state==GEN && core_ready && gap_left==0 && (!gaps || lfsr[2:0]!=0);
 wire keep_weight=DEPTHWISE || oc+weight_lane<co;
 assign cmd_ready=state==IDLE;
 assign mem_addr=state==WLOAD ? weight_address:
                 state==BREQ ? bb+(oc>>2)+bias_word:word_c[11:0];
 assign mem_en=(state==WLOAD && keep_weight)||state==BREQ||(vc && keep_c && core_ready);
 assign {out_channel,out_y,out_x}=tags[3];
 generate if(DEPTHWISE) begin: dw
 int8_dw16 core(.clk(clk),.reset(reset),.in_valid(req_valid),.in_ready(core_ready),
 .in_activation(response_zero ? 128'd0:mem_q),.in_weight(weight_q[127:0]),.in_bias(biases),
 .in_mask(response_meta[24:9]),.in_first(response_meta[26]),.in_last(response_meta[25]),
 .out_valid(out_valid),.out_ready(out_ready),.out_result(out_result),.out_overflow(out_overflow),.protocol_error(protocol_error));
 end else begin: dense
 wire [255:0] result;wire [7:0] overflow;
 int8_mac8x16 core(.clk(clk),.reset(reset),.in_valid(req_valid),.in_ready(core_ready),
 .in_activation(response_zero ? 128'd0:mem_q),.in_weight(weight_q),.in_bias(biases[255:0]),
 .in_mask(response_meta[24:9]),.in_first(response_meta[26]),.in_last(response_meta[25]),
 .out_valid(out_valid),.out_ready(out_ready),.out_result(result),.out_overflow(overflow),.protocol_error(protocol_error));
 assign out_result={256'd0,result};assign out_overflow={8'd0,overflow};
 end endgenerate
 always @(posedge clk) begin
 if(vc && core_ready) weight_q<=cache[meta_c[8:0]];
 if(wp_valid) begin
 if(DEPTHWISE) cache[wp_block]<={896'd0,mem_q};
 else if(wp_lane==7) cache[wp_block]<={wp_keep ? mem_q:128'd0,weight_collect[895:0]};
 else weight_collect[wp_lane*128+:128]<=wp_keep ? mem_q:128'd0;
 end end
 integer i;
 always @(posedge clk) begin
 if(reset) begin
 state<=IDLE;done<=0;failed<=0;h<=0;w<=0;ci<=0;co<=0;oh<=0;ow<=0;oc<=0;kernel<=0;stride<=0;pad<=0;
 ab<=0;wb<=0;bb<=0;blocks<=0;total_blocks<=0;weight_block<=0;weight_lane<=0;weight_address<=0;group_weight_base<=0;
 issue_index<=0;input_block<=0;kx<=0;ky<=0;bias_word<=0;ox<=0;oy<=0;pixels<=0;input_pixels<=0;
 activation_end<=0;weight_end<=0;bias_end<=0;bad_config<=0;gaps<=0;last_mask<=0;
 wp_valid<=0;wp_keep<=0;wp_lane<=0;wp_block<=0;biases<=0;
 va<=0;vb<=0;vc<=0;req_valid<=0;keep_b<=0;keep_c<=0;response_zero<=0;
 ix_a<=0;iy_a<=0;pixel_b<=0;word_c<=0;cb_a<=0;cb_b<=0;meta_a<=0;meta_b<=0;meta_c<=0;response_meta<=0;
 for(i=0;i<4;i=i+1) tags[i]<=0;
 group_outputs<=0;run_length<=0;gap_left<=0;lfsr<=32'h531bd092;
 chunks<=0;weight_bytes<=0;cache_groups<=0;feed_cycles<=0;max_run<=0;overlap_cycles<=0;
 end else begin
 lfsr<={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
 wp_valid<=state==WLOAD;
 if(state==WLOAD) begin wp_keep<=keep_weight;wp_lane<=weight_lane;wp_block<=weight_block;end
 if(core_ready) begin
 va<=issue;vb<=va;vc<=vb;req_valid<=vc;
 if(issue) begin
 ix_a<=$signed({1'b0,ox})*$signed({1'b0,stride})+$signed({1'b0,kx})-$signed({1'b0,pad});
 iy_a<=$signed({1'b0,oy})*$signed({1'b0,stride})+$signed({1'b0,ky})-$signed({1'b0,pad});
 cb_a<=DEPTHWISE ? (oc>>4):input_block;
 meta_a<={oc,oy,ox,issue_index==0,issue_index==total_blocks-1,
 (DEPTHWISE ? (oc+16>=ci ? last_mask:16'hffff):(input_block==blocks-1 ? last_mask:16'hffff)),issue_index};
 end
 pixel_b<=iy_a*$signed({1'b0,w})+ix_a;
 keep_b<=ix_a>=0 && iy_a>=0 && ix_a<$signed({1'b0,w}) && iy_a<$signed({1'b0,h});
 cb_b<=cb_a;meta_b<=meta_a;
 word_c<=$signed({1'b0,ab})+pixel_b*$signed({1'b0,blocks})+$signed({1'b0,cb_b});
 keep_c<=keep_b;meta_c<=meta_b;
 response_zero<=!keep_c;response_meta<=meta_c;
 tags[0]<=response_meta[74:27];for(i=1;i<4;i=i+1) tags[i]<=tags[i-1];
 end
 if(req_valid && core_ready) begin
 chunks<=chunks+1;run_length<=run_length+1;if(run_length+1>max_run) max_run<=run_length+1;
 if(vc) overlap_cycles<=overlap_cycles+1;
 end else run_length<=0;
 if(state==GEN || state==DRAIN) feed_cycles<=feed_cycles+1;
 if(out_valid && out_ready) group_outputs<=group_outputs+1;
 if(gap_left!=0) gap_left<=gap_left-1;
 case(state)
 IDLE:if(cmd_valid) begin
 h<=cfg_h;w<=cfg_w;ci<=cfg_ci;co<=cfg_co;oh<=cfg_oh;ow<=cfg_ow;oc<=0;
 kernel<=cfg_kernel;stride<=cfg_stride;pad<=cfg_pad;
 ab<=cfg_activation[15:4];wb<=cfg_weight[15:4];bb<=cfg_bias[15:4];blocks<=(cfg_ci+15)>>4;
 total_blocks<=DEPTHWISE ? (cfg_kernel==3 ? 9:1):(((cfg_ci+15)>>4)*(cfg_kernel==3 ? 9:1));
 input_pixels<=cfg_h*cfg_w;pixels<=cfg_oh*cfg_ow;gaps<=cfg_gaps;
 last_mask<=cfg_ci[3:0]==0 ? 16'hffff:(16'hffff>>(16-cfg_ci[3:0]));
 bad_config<=cfg_h==0 || cfg_w==0 || cfg_h>256 || cfg_w>256 || cfg_ci==0 || cfg_ci>960 ||
 cfg_co==0 || (DEPTHWISE ? cfg_co!=cfg_ci:cfg_co>64) || cfg_oh==0 || cfg_ow==0 || cfg_oh>256 || cfg_ow>256 ||
 (cfg_kernel!=1 && cfg_kernel!=3) || (cfg_stride!=1 && cfg_stride!=2) || cfg_pad>1 ||
 cfg_h+2*cfg_pad<cfg_kernel || cfg_w+2*cfg_pad<cfg_kernel ||
 (cfg_oh-1)*cfg_stride+cfg_kernel>cfg_h+2*cfg_pad || cfg_oh*cfg_stride+cfg_kernel<=cfg_h+2*cfg_pad ||
 (cfg_ow-1)*cfg_stride+cfg_kernel>cfg_w+2*cfg_pad || cfg_ow*cfg_stride+cfg_kernel<=cfg_w+2*cfg_pad ||
 (!DEPTHWISE && (((cfg_ci+15)>>4)*(cfg_kernel==3 ? 9:1))>511) ||
 |cfg_activation[3:0] || |cfg_weight[3:0] || |cfg_bias[3:0];
 done<=0;failed<=0;chunks<=0;weight_bytes<=0;cache_groups<=0;feed_cycles<=0;max_run<=0;overlap_cycles<=0;
 run_length<=0;gap_left<=0;state<=VALIDATE;
 end
 VALIDATE:begin
 activation_end<={ab,4'd0}+input_pixels*{blocks,4'd0};
 weight_end<={wb,4'd0}+(DEPTHWISE ? blocks:co)*total_blocks*16;
 bias_end<={bb,4'd0}+(DEPTHWISE ? ((co+15)>>4)*64:((co+7)>>3)*32);state<=CHECK;
 end
 CHECK:if(bad_config || activation_end>65536 || weight_end>65536 || bias_end>65536 || pixels>1024) begin failed<=1;done<=1;state<=IDLE;end
 else begin group_weight_base<=wb;state<=GROUP_INIT;end
 GROUP_INIT:begin weight_block<=0;weight_lane<=0;weight_address<=group_weight_base;state<=WLOAD;end
 WLOAD:begin
 if(keep_weight) weight_bytes<=weight_bytes+16;
 if(!DEPTHWISE && weight_lane!=7) begin weight_lane<=weight_lane+1;weight_address<=weight_address+total_blocks;end
 else if(weight_block+1<total_blocks) begin weight_lane<=0;weight_block<=weight_block+1;weight_address<=group_weight_base+weight_block+1;end
 else state<=WDRAIN;
 end
 WDRAIN:begin cache_groups<=cache_groups+1;bias_word<=0;state<=BREQ;end
 BREQ:state<=BTAKE;
 BTAKE:begin
 biases[bias_word*128+:128]<=mem_q;
 if(bias_word==(DEPTHWISE ? 3:1)) state<=GEN_INIT;else begin bias_word<=bias_word+1;state<=BREQ;end
 end
 GEN_INIT:begin issue_index<=0;input_block<=0;kx<=0;ky<=0;ox<=0;oy<=0;group_outputs<=0;gap_left<=0;state<=GEN;end
 GEN:if(issue) begin
 if(gaps && issue_index==1) gap_left<=7;
 if(issue_index+1<total_blocks) issue_index<=issue_index+1;else issue_index<=0;
 if(!DEPTHWISE && input_block+1<blocks) input_block<=input_block+1;
 else begin
 input_block<=0;
 if(kx+1<kernel) kx<=kx+1;
 else begin kx<=0;if(ky+1<kernel) ky<=ky+1;
 else begin ky<=0;if(ox+1<ow) ox<=ox+1;else if(oy+1<oh) begin ox<=0;oy<=oy+1;end else state<=DRAIN;end end
 end end
 DRAIN:if(group_outputs==pixels) begin
 if(oc+OUT_LANES<co) begin oc<=oc+OUT_LANES;group_weight_base<=group_weight_base+(DEPTHWISE ? total_blocks:(total_blocks<<3));state<=GROUP_INIT;end
 else begin done<=1;state<=IDLE;end
 end
 default:state<=IDLE;
 endcase
 end end
endmodule
