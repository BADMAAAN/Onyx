#version 440
layout(location=0) in vec2 qt_TexCoord0;
layout(location=0) out vec4 fragColor;
layout(std140,binding=0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float clock;
    float energy;
    float activity;
    float edge;
};
float field(vec2 p, float t) {
    for(int i=0;i<4;i++) {
        float k=float(i)+1.;
        p += .23*vec2(sin(p.y*k+t*.27),cos(p.x*k-t*.21))/k;
    }
    return .5+.5*sin(p.x*7.+sin(p.y*5.+t*.31)*1.7-t*.37);
}
void main() {
    vec2 uv=qt_TexCoord0;
    float t=clock*(.25+activity*.3);
    if(edge>.5) {
        float a=pow(uv.y,2.1)*sin(uv.x*3.14159)*(.16+energy*.18);
        float flow=.5+.5*sin(uv.x*18.-t);
        vec3 c=mix(vec3(.17,.3,.8),vec3(.49,.22,.66),flow);
        fragColor=vec4(c*a,a)*qt_Opacity; return;
    }
    vec2 p=(uv-.5)*2.;
    float r=length(p), aa=max(fwidth(r),.002);
    float sphere=1.-smoothstep(.73-aa,.73+aa,r);
    float f=field(p,t), g=field(p.yx*1.4+1.8,-t*.7);
    float strands=pow(max(0.,1.-abs(f-.5)*2.),5.)*.36;
    float depth=sqrt(max(0.,1.-r*r/.54));
    vec3 base=mix(vec3(.013,.018,.035),vec3(.105,.11,.14),depth*.65);
    vec3 tint=mix(vec3(.10,.26,.68),vec3(.37,.17,.58),g);
    tint=mix(tint,vec3(.58,.24,.46),pow(g,8.)*.32);
    base+=tint*(strands+pow(g,4.)*.20)*(.5+energy*.8+activity*.25)*depth;
    float actionPulse=smoothstep(.85,1.,activity)*pow(.5+.5*cos(atan(p.y,p.x)-clock*.8),4.);
    base+=tint*actionPulse*.08*depth;
    float rim=exp(-pow((r-.72)/.019,2.))*.18;
    base+=tint*rim;
    float glow=exp(-pow((r-.74)/.13,2.))*(1.-sphere)*(.09+energy*.17+activity*.025);
    float alpha=sphere+glow;
    fragColor=vec4(base*sphere+tint*glow,alpha)*qt_Opacity;
}
