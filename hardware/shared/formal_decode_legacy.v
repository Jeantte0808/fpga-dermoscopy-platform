`timescale 1ns/1ps
// Formal op3: per-branch requantization, logical-channel Concat, zero C16 tails.
// Op4: exact nearest2x copy. Each written word waits for its B response.
// Op2 is rejected: formal residual Add has a separate verified implementation.
module formal_decode_legacy #(parameter WATCHDOG=100000)(
 input clk,reset,cmd_valid,output cmd_ready,input [2:0] cfg_op,
 input [31:0] cfg_ma,cfg_mb,input [7:0] cfg_sa,cfg_sb,input cfg_bpa,cfg_bpb,
 input [15:0] cfg_h,cfg_w,cfg_ca,cfg_bh,cfg_bw,cfg_cb,cfg_oh,cfg_ow,cfg_co,
 input [29:0] cfg_a,cfg_b,cfg_output,
 output [29:0] awaddr,output [7:0] awlen,output awvalid,input awready,
 output [127:0] wdata,output wlast,wvalid,input wready,input [1:0] bresp,input bvalid,output bready,
 output [29:0] araddr,output [7:0] arlen,output arvalid,input arready,
 input [127:0] rdata,input [1:0] rresp,input rlast,rvalid,output rready,
 output reg done,failed,output reg [7:0] error_code,
 output reg [31:0] read_bytes,write_bytes,words_completed,valid_scalars,
 output aligned_valid,output [29:0] aligned_address,output [127:0] aligned_values
);
 localparam IDLE=0,GEOM=1,SIZES=2,CHECK=3,ROW=4,PIXEL=5,SCALE=6,ADDRESS=7,PLAN=8,
 RA=9,RA_WAIT=10,RB=11,RB_WAIT=12,RC=13,RC_WAIT=14,ALIGN=15,WRITE=16,WRITE_WAIT=17,ADVANCE=18,FINISH=19,PACK=20,QSTART=21,QWAIT=22;
 reg [4:0] state;reg [2:0] op;reg [31:0] ma,mb;reg [7:0] sa,sb;reg bpa,bpb;
 reg [15:0] h,w,ca,bh,bw,cb,oh,ow,co,acp,bcp,ocp,y,x,c;
 reg [29:0] ab,bb,ob,aaddr,baddr,oaddr;
 reg [16:0] apixels,bpixels,opixels,arow,brow,orow,apixel,bpixel,opixel;
 reg [31:0] asize,bsize,osize;reg [26:0] abyte,bbyte,obyte;
 reg need_a,need_b,need_c;reg [15:0] bword;
 reg [4:0] a_count,keep_count;reg [3:0] bshift;
 reg [127:0] aval,bval,cval,result;reg [255:0] b_aligned;
 assign cmd_ready=state==IDLE&&!failed;
 wire dr,dd,df,dwe;wire [7:0] de;wire [31:0] db;wire [127:0] rd;
 wire issuing=state==RA||state==RB||state==RC;
 wire [29:0] read_address=state==RA ? aaddr:state==RB ? baddr:baddr+16;
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(1),.WATCHDOG(WATCHDOG)) reader(
 .clk(clk),.reset(reset),.cmd_valid(issuing),.cmd_ready(dr),.cmd_write(1'b0),.cmd_base(read_address),.cmd_stride(30'd0),.cmd_row_bytes(16'd16),.cmd_rows(16'd1),
 .done(dd),.failed(df),.error_code(de),.transferred_bytes(db),.awready(1'b0),.wready(1'b0),.bresp(2'd0),.bvalid(1'b0),
 .araddr(araddr),.arlen(arlen),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rresp(rresp),.rlast(rlast),.rvalid(rvalid),.rready(rready),.mem_we(dwe),.mem_wdata(rd),.mem_rdata(128'd0));
 wire wr,wd,wf;wire [7:0] we;wire [31:0] wb;
 axi_tile_dma_guarded #(.WORD_ADDR_BITS(1),.WATCHDOG(WATCHDOG)) writer(
 .clk(clk),.reset(reset),.cmd_valid(state==WRITE),.cmd_ready(wr),.cmd_write(1'b1),.cmd_base(oaddr),.cmd_stride(30'd0),.cmd_row_bytes(16'd16),.cmd_rows(16'd1),
 .done(wd),.failed(wf),.error_code(we),.transferred_bytes(wb),
 .awaddr(awaddr),.awlen(awlen),.awvalid(awvalid),.awready(awready),.wdata(wdata),.wlast(wlast),.wvalid(wvalid),.wready(wready),.bresp(bresp),.bvalid(bvalid),.bready(bready),
 .arready(1'b0),.rdata(128'd0),.rresp(2'd0),.rlast(1'b0),.rvalid(1'b0),.mem_rdata(result));
 wire overlap_a=ob<({1'b0,ab}+asize)&&ab<({1'b0,ob}+osize);
 wire overlap_b=ob<({1'b0,bb}+bsize)&&bb<({1'b0,ob}+osize);
 wire [15:0] first_b=c>ca ? c-ca:16'd0;
 wire [15:0] last_channel=c+15<co ? c+15:co-1;
 wire [127:0] b_packed=b_aligned[127:0]<<{a_count,3'b0};
 reg [511:0] qvalues,qms;reg [95:0] qss;reg [15:0] qmask;
 wire qready,qvalid;wire [127:0] qout;wire [15:0] qerror;wire [63:0] qtag;
 requant_vector16 aligner(.clk(clk),.reset(reset),.in_valid(state==QSTART),.in_ready(qready),
 .in_values(qvalues),.in_multipliers(qms),.in_shifts(qss),.in_zero_points(256'd0),
 .in_min({16{8'h81}}),.in_max({16{8'h7f}}),.in_residual(128'd0),.in_add_residual(16'd0),.in_mask(qmask),.in_tag({34'd0,oaddr}),
 .out_valid(qvalid),.out_ready(state==QWAIT),.out_values(qout),.out_mask(),.out_error(qerror),.out_tag(qtag));
 assign aligned_valid=qvalid&&state==QWAIT&&qerror==0;
 assign aligned_address=qtag[29:0];assign aligned_values=qout;
 integer j;reg signed [8:0] sum;reg [127:0] packed_word;
 always @* begin
 packed_word=0;sum=0;
 for(integer lane=0;lane<16;lane=lane+1)if(c+lane<co)begin
 if(op==2)begin
 sum=$signed({aval[lane*8+7],aval[lane*8+:8]})+$signed({bval[lane*8+7],bval[lane*8+:8]});
 packed_word[lane*8+:8]=sum>127 ? 8'h7f:sum< -128 ? 8'h80:sum[7:0];
 end else if(op==4||c+lane<ca)packed_word[lane*8+:8]=aval[lane*8+:8];
 else packed_word[lane*8+:8]=b_packed[lane*8+:8];
 end end
 always @(posedge clk)begin
 if(reset)begin
 state<=IDLE;op<=0;qvalues<=0;qms<=0;qss<=0;qmask<=0;ma<=1;mb<=1;sa<=0;sb<=0;bpa<=0;bpb<=0;h<=0;w<=0;ca<=0;bh<=0;bw<=0;cb<=0;oh<=0;ow<=0;co<=0;acp<=0;bcp<=0;ocp<=0;y<=0;x<=0;c<=0;ab<=0;bb<=0;ob<=0;aaddr<=0;baddr<=0;oaddr<=0;
 apixels<=0;bpixels<=0;opixels<=0;arow<=0;brow<=0;orow<=0;apixel<=0;bpixel<=0;opixel<=0;asize<=0;bsize<=0;osize<=0;abyte<=0;bbyte<=0;obyte<=0;need_a<=0;need_b<=0;need_c<=0;bword<=0;a_count<=0;keep_count<=0;bshift<=0;aval<=0;bval<=0;cval<=0;result<=0;b_aligned<=0;
 done<=0;failed<=0;error_code<=0;read_bytes<=0;write_bytes<=0;words_completed<=0;valid_scalars<=0;
 end else begin done<=0;
 if(dwe)begin if(state==RA_WAIT)aval<=rd;else if(state==RB_WAIT)bval<=rd;else if(state==RC_WAIT)cval<=rd;end
 case(state)
 IDLE:if(cmd_valid&&cmd_ready)begin
 op<=cfg_op;ma<=cfg_ma;mb<=cfg_mb;sa<=cfg_sa;sb<=cfg_sb;bpa<=cfg_bpa;bpb<=cfg_bpb;h<=cfg_h;w<=cfg_w;ca<=cfg_ca;bh<=cfg_bh;bw<=cfg_bw;cb<=cfg_cb;oh<=cfg_oh;ow<=cfg_ow;co<=cfg_co;ab<=cfg_a;bb<=cfg_b;ob<=cfg_output;
 y<=0;x<=0;c<=0;read_bytes<=0;write_bytes<=0;words_completed<=0;valid_scalars<=0;state<=GEOM;end
 GEOM:begin acp<=((ca+15)>>4)<<4;bcp<=((cb+15)>>4)<<4;ocp<=((co+15)>>4)<<4;apixels<=h[8:0]*w[8:0];bpixels<=bh[8:0]*bw[8:0];opixels<=oh[8:0]*ow[8:0];state<=SIZES;end
 SIZES:begin asize<=apixels*acp[9:0];bsize<=bpixels*bcp[9:0];osize<=opixels*ocp[9:0];state<=CHECK;end
 CHECK:if((op!=3&&op!=4)||h==0||w==0||h>256||w>256||ca==0||ca>960||co==0||co>960||oh==0||ow==0||oh>256||ow>256||ab[3:0]!=0||ob[3:0]!=0||{1'b0,ab}+asize>31'h40000000||{1'b0,ob}+osize>31'h40000000||overlap_a||
 (op==4 ? (co!=ca||cb!=0||oh>2*h||oh+1<2*h||ow>2*w||ow+1<2*w):
 (bh!=h||bw!=w||oh!=h||ow!=w||cb==0||cb>960||(!bpa&&(ma==0||ma[31]||sa>63))||(!bpb&&(mb==0||mb[31]||sb>63))||bb[3:0]!=0||{1'b0,bb}+bsize>31'h40000000||overlap_b||(op==2 ? (ca!=cb||co!=ca):co!=ca+cb))))
 begin failed<=1;error_code<=6;state<=FINISH;end else state<=ROW;
 ROW:begin arow<=(op==4 ? y[8:1]:y[8:0])*w[8:0];brow<=y[8:0]*w[8:0];orow<=y[8:0]*ow[8:0];state<=PIXEL;end
 PIXEL:begin apixel<=arow+(op==4 ? x[8:1]:x[8:0]);bpixel<=brow+x[8:0];opixel<=orow+x[8:0];state<=SCALE;end
 SCALE:begin abyte<=apixel*acp[9:0];bbyte<=bpixel*bcp[9:0];obyte<=opixel*ocp[9:0];state<=ADDRESS;end
 ADDRESS:begin
 aaddr<=ab+abyte+c;oaddr<=ob+obyte+c;
 bword<=op==2 ? c:{first_b[15:4],4'b0};
 baddr<=bb+bbyte+(op==2 ? c:{first_b[15:4],4'b0});state<=PLAN;end
 PLAN:begin
 aval<=0;bval<=0;cval<=0;
 need_a<=op!=3||c<ca;need_b<=op==2||(op==3&&last_channel>=ca);need_c<=op==3&&last_channel>=ca&&last_channel-ca>=({first_b[15:4],4'b0}+16);
 a_count<=op==3 ? (c>=ca ? 0:ca-c>16 ? 16:ca-c):0;bshift<=op==3 ? first_b[3:0]:0;
 keep_count<=co-c>=16 ? 16:co-c;
 if(op!=3||c<ca)state<=RA;else state<=RB;
 end
 RA:if(dr)state<=RA_WAIT;
 RA_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=need_b ? RB:ALIGN;end end
 RB:if(dr)state<=RB_WAIT;
 RB_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=need_c ? RC:ALIGN;end end
 RC:if(dr)state<=RC_WAIT;
 RC_WAIT:if(dd)begin if(df)begin failed<=1;error_code<=de;state<=FINISH;end else begin read_bytes<=read_bytes+db;state<=ALIGN;end end
 ALIGN:begin b_aligned<={cval,bval}>>{bshift,3'b0};state<=PACK;end
 PACK:begin
 if(op==4)begin result<=packed_word;state<=WRITE;end
 else begin
 for(j=0;j<16;j=j+1)begin
 qvalues[j*32+:32]<={{24{packed_word[j*8+7]}},packed_word[j*8+:8]};
 qms[j*32+:32]<=c+j<ca ? (bpa ? 32'd1:ma):(bpb ? 32'd1:mb);
 qss[j*6+:6]<=c+j<ca ? (bpa ? 6'd0:sa[5:0]):(bpb ? 6'd0:sb[5:0]);
 qmask[j]<=c+j<co;
 end
 state<=QSTART;end end
 QSTART:if(qready)state<=QWAIT;
 QWAIT:if(qvalid)begin
 if(qerror!=0)begin failed<=1;error_code<=14;state<=FINISH;end
 else begin result<=qout;state<=WRITE;end end
 WRITE:if(wr)state<=WRITE_WAIT;
 WRITE_WAIT:if(wd)begin if(wf)begin failed<=1;error_code<=we;state<=FINISH;end else begin write_bytes<=write_bytes+wb;words_completed<=words_completed+1;valid_scalars<=valid_scalars+keep_count;state<=ADVANCE;end end
 ADVANCE:if(c+16<co)begin c<=c+16;state<=ADDRESS;end
 else begin c<=0;if(x+1<ow)begin x<=x+1;state<=ROW;end else begin x<=0;if(y+1<oh)begin y<=y+1;state<=ROW;end else state<=FINISH;end end
 FINISH:begin done<=1;state<=IDLE;end
 default:begin failed<=1;error_code<=8;state<=FINISH;end
 endcase end end
endmodule
