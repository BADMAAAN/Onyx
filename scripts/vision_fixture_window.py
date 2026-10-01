"""Real Windows image fixture for semantic Vision acceptance (no text hints)."""
import tkinter as tk
from PIL import Image,ImageDraw,ImageTk

root=tk.Tk()
root.title('KK vision acceptance fixture')
root.geometry('700x430+100+100')
root.attributes('-topmost',True)
image=Image.new('RGB',(680,380),'white')
draw=ImageDraw.Draw(image)
draw.ellipse((60,90,260,290),fill='red')
draw.rectangle((400,90,600,290),fill='blue')
photo=ImageTk.PhotoImage(image)
tk.Label(root,image=photo).pack()
root.after(90000,root.destroy)
root.mainloop()
