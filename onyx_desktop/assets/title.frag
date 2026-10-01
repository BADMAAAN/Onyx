#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf { mat4 qt_Matrix; float qt_Opacity; float gradientAmount; vec2 sampleStep; };
layout(binding = 1) uniform sampler2D source;
void main() {
    float a = texture(source, qt_TexCoord0).a;
    vec3 spectrum = mix(vec3(0.90,0.63,0.96),vec3(0.55,0.88,1.0),qt_TexCoord0.x);
    vec3 color = mix(vec3(0.98,0.98,1.0), spectrum, gradientAmount);
    float glow=0.0;
    for(int y=-2;y<=2;y++) for(int x=-2;x<=2;x++)
        glow+=texture(source,qt_TexCoord0+vec2(x,y)*sampleStep).a;
    glow=glow/25.0*0.3*(1.0-a);
    fragColor=vec4(color*(a+glow),a+glow)*qt_Opacity;
}
