"""Procedural dark-glass KK sphere based on the official visual reference."""
import math
import numpy as np
from PIL import Image, ImageDraw
from kk.core.state import AgentState


class OrbRenderer:
    def __init__(self, size):
        self.size = size
        axis = (np.arange(size, dtype=np.float32) + .5 - size / 2) / (size * .42)
        self.x, self.y = np.meshgrid(axis, axis)
        self.radius = np.hypot(self.x, self.y)
        self.angle = np.arctan2(self.y, self.x)
        self.level = self.activity = self.phase = 0.

    def render(self, state, level=0., dt=1/30, mic_visible=False):
        dt = min(max(dt, 0.), .1)
        target = {AgentState.LISTENING: .3, AgentState.USER_SPEAKING: .5,
                  AgentState.THINKING: .8, AgentState.ACTING: .7,
                  AgentState.SPEAKING: .6, AgentState.RECORDING: .7,
                  AgentState.COMMITTING: .8, AgentState.MIC_READY: .2}.get(state, .06)
        self.level += (min(1., max(0., level)) - self.level) * (1-math.exp(-dt*8))
        self.activity += (target-self.activity) * (1-math.exp(-dt*3))
        self.phase += dt * (.18 + self.activity*.65)
        t, energy = self.phase, self.level
        scale = 1 + .008*math.sin(t*1.7) + .025*energy
        x, y, r = self.x/scale, self.y/scale, self.radius/scale
        edge = np.clip((1-r)*self.size*.42+.5, 0, 1)
        z = np.sqrt(np.clip(1-r*r, 0, 1))
        rgb = np.zeros((*r.shape, 3), dtype=np.float32)
        rgb[:] = (7, 10, 24)
        rgb += z[..., None] * np.array([6, 9, 19], np.float32)
        palette = np.array([[35, 198, 255], [98, 82, 245], [240, 91, 220]], np.float32)
        # Broad translucent S-shaped sheets with narrow luminous edges.
        for i, offset in enumerate((-.52, -.07, .46)):
            tilt = .24*math.sin(t*.47+i*1.8)
            u = x*math.cos(tilt)-y*math.sin(tilt)
            v = x*math.sin(tilt)+y*math.cos(tilt)
            curve = offset*z + .29*np.sin(u*3.2+t*.48+i*.72)
            d = v-curve
            width = .23+.10*np.cos(u*2.4+i+t*.2)
            ribbon = np.exp(-((d-width*.46)/(width*.7))**2)
            line = np.exp(-(d/.015)**2)
            trailing = np.exp(-((d-width)/.012)**2)
            halo = np.exp(-(d/.065)**2)
            mix = np.clip(.5+.48*np.sin(u*2.3+t*.23+i*1.9), 0, 1)
            color = palette[i]*(1-mix[..., None])+palette[(i+1)%3]*mix[..., None]
            visibility = np.clip(z*1.6, 0, 1)
            rgb += color * ((.42*ribbon+.28*halo+.9*line+.2*trailing)*visibility)[..., None]
            rgb += (line*visibility*.38)[..., None]*np.array([160, 178, 190])
        rim = np.exp(-((r-.971)/.018)**2)
        inner_rim = np.exp(-((r-.928)/.055)**2)
        mix = (.5+.5*np.sin(self.angle*2+t*.3))[..., None]
        rim_color = palette[0]*(1-mix)+palette[2]*mix
        rgb += rim_color * (rim*.9+inner_rim*.32)[..., None]
        glint = np.exp(-((x+.24)/.65)**2-((y+.81)/.095)**2)
        rgb += glint[..., None]*np.array([74, 97, 124])
        rgb *= 1 + .12*energy
        glow = np.exp(-((r-1)/.055)**2)*(.14+.10*energy)
        alpha = np.where(r <= 1, edge, glow)
        alpha = np.where(r < 1.14, alpha, 0)
        rgb = np.where((r > 1)[..., None], rim_color, rgb)
        rgba = np.dstack((np.clip(rgb, 0, 255), np.clip(alpha*255, 0, 255))).astype(np.uint8)
        image = Image.fromarray(rgba)
        if mic_visible or state in {AgentState.RECORDING, AgentState.COMMITTING, AgentState.MIC_READY}:
            draw = ImageDraw.Draw(image)
            s = self.size
            x, y, r = s*.5, s*.74, s*.1
            color = (255, 140, 190, 235) if state == AgentState.RECORDING else (190, 225, 255, 215)
            draw.ellipse((x-r,y-r,x+r,y+r), fill=(12,18,38,230), outline=color)
            draw.rounded_rectangle((x-r*.25,y-r*.65,x+r*.25,y+r*.2), radius=r*.24, outline=color, width=max(1,round(s/100)))
            draw.arc((x-r*.5,y-r*.35,x+r*.5,y+r*.5), 0,180,fill=color,width=2)
            draw.line((x,y+r*.5,x,y+r*.75),fill=color,width=2)
        return image
