`timescale 1ns/1ps
// FCP1 is an isolated candidate protocol, not the accepted RGB demo protocol.
// Metadata and words are from a complete-packet validated CDC. No command is
// exposed until header and payload have both passed the byte CRC checks.
module formal_command_rx #(parameter [255:0] IMAGE_ID=0,parameter WATCHDOG=100000)(
 input clk,reset,
 input meta_valid,output meta_ready,input [15:0] meta_length,meta_port,
 input [47:0] meta_mac,input [31:0] meta_ip,
 input data_valid,output data_ready,input [127:0] data,input [15:0] keep,input data_last,
 input [47:0] peer_mac,input [31:0] peer_ip,input [15:0] peer_port,
 output req_valid,input req_ready,output reg [7:0] req_error,
 output [7:0] req_opcode,output [31:0] req_txn,req_generation,req_frame,req_index,
 output [15:0] req_bytes,output [31:0] req_crc,req_frame_crc,output [255:0] req_image_id,
 output [47:0] req_mac,output [31:0] req_ip,output [15:0] req_port,
 output payload_valid,input payload_ready,output [127:0] payload,output payload_last,
 output busy,output reg failed,output reg [7:0] fatal_error
);
 localparam IDLE=0,CAPTURE=1,FETCH=2,LOAD_WORD=3,CHECK_BYTE=4,VALIDATE=5,REQUEST=6,
            BODY_FETCH=7,BODY_LOAD=8,BODY_SEND=9,FAULT=10;
 reg [3:0] state;reg [767:0] header;
 (* ram_style="block" *) reg [127:0] ram[0:69];reg [127:0] rq,hold_word;
 reg [6:0] write_index,read_index;reg [15:0] length,remaining,position;
 reg [3:0] lane;reg [31:0] header_crc,body_crc,timer;
 reg [47:0] source_mac;reg [31:0] source_ip;reg [15:0] source_port;
 reg shape_bad,peer_bad;reg [31:0] body_words,body_sent;
 function [31:0] be32;input [767:0] h;input integer k;begin be32={h[k*8+:8],h[(k+1)*8+:8],h[(k+2)*8+:8],h[(k+3)*8+:8]};end endfunction
 function [31:0] crcbyte;input [31:0] c;input [7:0] b;integer j;reg [31:0] t;begin
 t=c^b;for(j=0;j<8;j=j+1)t=t[0]?(t>>1)^32'hedb88320:t>>1;crcbyte=t;end endfunction
 assign req_opcode=header[5*8+:8];assign req_txn=be32(header,8);assign req_generation=be32(header,12);
 assign req_frame=be32(header,16);assign req_index=be32(header,20);assign req_bytes=be32(header,24);
 assign req_crc=be32(header,28);assign req_frame_crc=be32(header,32);
 genvar g;generate for(g=0;g<32;g=g+1)begin:ID assign req_image_id[255-g*8-:8]=header[(40+g)*8+:8];end endgenerate
 assign req_mac=source_mac;assign req_ip=source_ip;assign req_port=source_port;
 wire [7:0] byte_value=hold_word[lane*8+:8];
 wire [7:0] header_byte=(position>=72&&position<76)?8'd0:byte_value;
 wire [15:0] expected_keep=remaining>=16?16'hffff:(16'hffff>>(16-remaining));
 wire canonical_zero=req_frame==0&&req_index==0&&be32(header,24)==0&&req_crc==0&&req_frame_crc==0;
 wire control_empty=be32(header,24)==0&&req_crc==0;
 wire bad_shape=(req_opcode==1||req_opcode==3)?!canonical_zero:
 req_opcode==2?(req_frame!=0||req_index>=2931||be32(header,24)==0||be32(header,24)>1024||req_bytes[3:0]!=0||req_frame_crc!=0):
 req_opcode==4?(!control_empty||req_frame==0||req_index!=0):
 req_opcode==5?(req_frame==0||req_index>=192||be32(header,24)!=1024||req_frame_crc!=0):
 req_opcode==6?(!control_empty||req_frame==0||req_index>=64||req_frame_crc!=0):
 req_opcode==7?(!control_empty||req_frame==0||req_index!=0||req_frame_crc!=0):
 req_opcode==8?((!canonical_zero&&!(req_frame==0&&control_empty&&req_frame_crc==0&&req_index[31:16]==16'h5052&&req_index[15:12]==0&&req_index[11:4]<=82&&req_index[3:0]<3))||req_generation!=0):1'b1;
 wire reserved_bad=header[6*8+:16]!=0||be32(header,36)!=0||header[76*8+:160]!=0;
 assign meta_ready=state==IDLE&&!failed;assign data_ready=state==CAPTURE&&!failed;
 assign req_valid=state==REQUEST&&!failed;
 assign payload_valid=state==BODY_SEND&&!failed;assign payload=hold_word;
 assign payload_last=body_sent+1==body_words;assign busy=state!=IDLE;
 always @(posedge clk)begin
 rq<=ram[read_index];
 if(data_valid&&data_ready&&write_index<70)ram[write_index]<=data;
 end
 always @(posedge clk)if(reset)begin
 state<=IDLE;header<=0;hold_word<=0;write_index<=0;read_index<=0;length<=0;remaining<=0;position<=0;lane<=0;
 header_crc<=32'hffffffff;body_crc<=32'hffffffff;timer<=0;source_mac<=0;source_ip<=0;source_port<=0;
 shape_bad<=0;peer_bad<=0;body_words<=0;body_sent<=0;req_error<=0;failed<=0;fatal_error<=0;
 end else begin
 if(state==CAPTURE)begin
 if(data_valid&&data_ready)timer<=0;else timer<=timer+1'b1;
 if(timer>=WATCHDOG-1)begin failed<=1;fatal_error<=144;state<=FAULT;end
 end else timer<=0;
 case(state)
 IDLE:if(meta_valid&&meta_ready)begin
 source_mac<=meta_mac;source_ip<=meta_ip;source_port<=meta_port;
 peer_bad<=meta_mac!=peer_mac||meta_ip!=peer_ip||meta_port!=peer_port;
 length<=meta_length;remaining<=meta_length;write_index<=0;read_index<=0;
 header<=0;position<=0;lane<=0;header_crc<=32'hffffffff;body_crc<=32'hffffffff;req_error<=0;
 shape_bad<=meta_length<96||meta_length>1120;
 if(meta_length==0)begin req_error<=128;state<=REQUEST;end else state<=CAPTURE;
 end
 CAPTURE:if(data_valid&&data_ready)begin
 if(write_index<6)header[write_index*128+:128]<=data;
 if(write_index<127)write_index<=write_index+1'b1;
 if(keep!=expected_keep||data_last!=(remaining<=16)||remaining==0||write_index>=70)shape_bad<=1;
 remaining<=remaining>16?remaining-16:0;
 if(data_last)begin read_index<=0;state<=FETCH;end
 end
 FETCH:state<=LOAD_WORD;
 LOAD_WORD:begin hold_word<=rq;lane<=0;state<=CHECK_BYTE;end
 CHECK_BYTE:begin
 if(position<96)header_crc<=crcbyte(header_crc,header_byte);else body_crc<=crcbyte(body_crc,byte_value);
 position<=position+1'b1;lane<=lane+1'b1;
 if(position+1>=length||position>=1119)state<=VALIDATE;
 else if(lane==15)begin read_index<=read_index+1'b1;state<=FETCH;end
 end
 VALIDATE:begin
 req_error<=shape_bad?8'd128:peer_bad?8'd129:
 be32(header,0)!=32'h46435031||header[4*8+:8]!=1?8'd130:
 reserved_bad?8'd131:(header_crc^32'hffffffff)!=be32(header,72)?8'd132:
 req_image_id!=IMAGE_ID?8'd133:
 be32(header,24)!=length-96||bad_shape||req_txn==0?8'd134:
 (body_crc^32'hffffffff)!=req_crc?8'd135:8'd0;
 body_words<=be32(header,24)>>4;body_sent<=0;state<=REQUEST;
 end
 REQUEST:if(req_ready)begin
 if(req_error!=0||req_bytes==0)state<=IDLE;
 else begin read_index<=6;state<=BODY_FETCH;end end
 BODY_FETCH:state<=BODY_LOAD;
 BODY_LOAD:begin hold_word<=rq;state<=BODY_SEND;end
 BODY_SEND:if(payload_ready)begin
 body_sent<=body_sent+1'b1;
 if(payload_last)state<=IDLE;else begin read_index<=read_index+1'b1;state<=BODY_FETCH;end
 end
 FAULT:state<=FAULT;
 default:begin failed<=1;fatal_error<=145;state<=FAULT;end
 endcase
 end
endmodule
