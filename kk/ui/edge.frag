#version 440
layout(location=0) in vec2 qt_TexCoord0;
layout(location=0) out vec4 fragColor;
layout(std140,binding=0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 viewport;
    vec2 origin;
    vec2 extent;
    float energy;
    float halo;
};
void main() {
    vec2 p = origin + qt_TexCoord0 * extent;
    vec2 farEdge = viewport-p;
    float d = min(min(p.x,farEdge.x),min(p.y,farEdge.y));
    // Broad stationary cold white: no ribbon, core, phase or corner summation.
    float glow = exp(-pow(d/max(halo*.48,1.),2.));
    float cutoff = 1.-smoothstep(halo*.72,halo,d);
    float alpha = glow*cutoff*.27*(1.+clamp(energy,0.,1.)*.08);
    vec3 color = vec3(.84,.88,.98);
    fragColor = vec4(color*alpha,alpha)*qt_Opacity;
}
