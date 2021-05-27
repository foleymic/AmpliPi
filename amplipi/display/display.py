#!/usr/bin/env python3
#
# Requirements:
# pip: adafruit-circuitpython-rgb-display pillow numpy
# apt: libatlas-base-dev
#
# TODO:
# Clear screen on early exit/failure or while running provide some other
#   indication that the display is still being updated.
# Handle connection lost better
# Fix when play icons appear to match web
# Debounce touches

import argparse
import board
import busio
import cProfile
import digitalio
import pwmio
import RPi.GPIO as GPIO
import requests
import socket
import time

# Display
import adafruit_rgb_display.ili9341 as ili9341
from PIL import Image, ImageDraw, ImageFont

# To retrieve system info
import netifaces as ni    # network interfaces
import psutil             # CPU, RAM, etc.


profile = False
update_period = 2.0       # Display update rate in seconds
is_amplipi = False         # Set to false for PoE board test setup

# Configuration for extra TFT pins:
clk_pin = board.SCLK_2 if is_amplipi else board.SCLK
mosi_pin = board.MOSI_2 if is_amplipi else board.MOSI
miso_pin = board.MISO_2 if is_amplipi else board.MISO

cs_pin = board.D44 if is_amplipi else board.CE0
dc_pin = board.D39 if is_amplipi else board.D25
led_pin = board.D12 if is_amplipi else board.D18
rst_pin = None
t_cs_pin = board.D45 if is_amplipi else board.D5
t_irq_pin = board.D38 if is_amplipi else board.D6

# Network interface name to get IP address of
iface_name = "eth0"

# Number of screens to scroll through
NUM_SCREENS = 1
_active_screen = 0

################################################################################
# A note on Raspberry Pi (BCM2837B0) clocks
################################################################################
# The SPI frequency is based on the "core clock" of the BCM2837B0
# See Section 2.3.1 of BCM2837B0-ARM-Peripherals.pdf, CLK Register CDIV field:
#   SPIx_CLK = system_clock_freq / [2*(speed_field + 1)]
# Best I can tell 'system_clock_freq' is the same as 'core_freq'
# speed_field is 12 bits, so SPI freq is [core_freq/2^13, core_freq/2]
# For a core_freq of 400 MHz: [48.8 kHz, 200 MHz]
#
# To lock the core clock to 400 MHz, in /boot/config.txt set:
#   core_freq=400
#   core_freq_min=400
#
# Also needed to enable SPI1:
#   dtparam=spi=on
#   dtoverlay=spi1-2cs
#
# ARM (CPU) clock frequency in Hz:  vcgencmd measure_clock arm
# Core clock frequency in Hz:       vcgencmd measure_clock core
#
# Specify the max baud rate here. This will be used to find the 'speed_field'
# such that 400*10^6 / [2*(speed_field + 1)] <= 'spi_baud'
# The ILI9341 specifies a max write rate of 10 MHz, and a max read rate of
# 6.66 MHz but much faster speeds seem to work okay.
spi_baud = 16 * 10**6

parser = argparse.ArgumentParser(description='Display AmpliPi Information on a TFT Display')
parser.add_argument('url', nargs='?', default="localhost", help="The AmpliPi's URL to contact")
args = parser.parse_args()

# Convert number range to color gradient (min=green, max=red)
def gradient(num, min_val=0, max_val=100):
  #red = round(255*(val - min_val) / (max_val - min_val))
  #grn = round(255-red)#255*(max_val - val) / (max_val - min_val)
  mid = (min_val + max_val) / 2
  scale = 255 / (mid - min_val)
  if num <= min_val:
    red = 0
    grn = 255
  elif num >= max_val:
    red = 255
    grn = 0
  elif num < mid:
    red = round(scale * (num - min_val))
    grn = 255
  else:
    red = 255
    grn = 255 - round(scale * (num - mid))
  return f'#{red:02X}{grn:02X}00'

def get_amplipi_data():
  sources = [{'name': '', 'playing': False} for i in range(4)]
  zones = []
  try:
    # TODO: If the AmpliPi server isn't available at this url, there is a
    # 5-second delay introduced by socket.getaddrinfo
    r = requests.get('http://' + args.url + '/api/', timeout=0.1)
    if r.status_code == 200:
      j = r.json()
      stream_ids = [s['id'] for s in j['streams']]
      for i in range(len(j['sources'])):
        inp = j['sources'][i]['input']
        playing = False
        if inp.startswith('stream='):
          sid = int(inp.replace('stream=', ''))
          if sid in stream_ids:
            strm = j['streams'][stream_ids.index(sid)]
            name = strm['name']
            playing = strm['status'] == 'playing'
          else:
            name = "INVALID STREAM"
        else:
          name = inp
        sources[i]['name'] = name
        sources[i]['playing'] = playing
      # For the crazies out there
      #sources = [j['streams'][stream_ids.index(int(s['input'].replace('stream=', '')))]['name']
      #           if s['input'].startswith('stream=') else s['input'] for s in j['sources']]
      zones = j['zones']
    else:
      print('Error: bad status code returned from amplipi')
  except requests.ConnectionError as e:
    print("Error: couldn't connect to", args.url, e.args[0].reason)
  except requests.Timeout:
    print('Error: timeout requesting amplipi status')
  except ValueError:
    print('Error: invalid json in amplipi status response')

  return sources, zones

# Draw volumes on bars.
# Draw is a PIL drawing surface
# zones is a list of (names, volumes) of size [6, 12, 18, 24, 30, 36]
# (x,y) is the top-left of the volume bar area
def draw_volume_bars(draw, font, small_font, zones, x=0, y=0, width=320, height=240):
  n = len(zones)
  if n == 0: # No zone info from AmpliPi server
    pass
  elif n <= 6: # Draw horizonal bars
    wb = int(width / 2)                   # Each bar's full width
    hb = 12                               # Each bar's height
    sp = int((height - n*hb) / (2*(n-1))) # Spacing between bars
    xb = width - wb
    vol2pix = wb / -78
    for i in range(n):
      yb = y + i*hb + 2*i*sp # Bar starting y-position

      # Draw zone name as text
      draw.text((x, yb), zones[i]['name'], font=font, fill='#FFFFFF')

      # Draw background of volume bar
      draw.rectangle(((xb, int(yb+2), xb+wb, int(yb+hb))), fill='#999999')

      # Draw volume bar
      if zones[i]['vol'] > -79:
        color = '#666666' if zones[i]['mute'] else '#0080ff'
        xv = xb + (wb - round(zones[i]['vol'] * vol2pix))
        draw.rectangle(((xb, int(yb+2), xv, int(yb+hb))), fill=color)
  elif n <= 18: # Draw vertical bars
    # Get the pixel height of a character, and add vertical margins
    ch = small_font.getbbox('0', anchor='lt')[3] + 4
    wb = 12                               # Each bar's width
    sp = int((width - n*wb) / (2*(n-1)))  # Spacing between bars
    yt = y + height - ch                  # Text top y-position
    vol2pix = (height - ch) / -78         # dB to pixels conversion factor
    for i in range(n):
      xb = x + i*wb + 2*i*sp # Bar starting x-position

      # Draw zone number as centered text
      draw.text((xb + round(wb/2), y + height - round(ch/2)), str(i+1),
                anchor="mm", font=small_font, fill='#FFFFFF')

      # Draw background of volume bar
      draw.rectangle(((xb, y, xb+wb, yt)), fill='#999999')

      # Draw volume bar
      if zones[i]['vol'] > -79:
        color = '#666666' if zones[i]['mute'] else '#0080ff'
        yv = y + round(zones[i]['vol'] * vol2pix)
        draw.rectangle(((xb, yv, xb+wb, yt)), fill=color)
  else:
    print("Error: can't display more than 18 volumes")


# Pins
disp_cs = digitalio.DigitalInOut(cs_pin)
disp_dc = digitalio.DigitalInOut(dc_pin)
touch_cs = digitalio.DigitalInOut(t_cs_pin)
touch_cs.direction = digitalio.Direction.OUTPUT
touch_cs.value = True

# Setup SPI bus using hardware SPI:
spi = busio.SPI(clock=clk_pin, MOSI=mosi_pin, MISO=miso_pin)

# Create the ILI9341 display:
display = ili9341.ILI9341(spi, cs=disp_cs, dc=disp_dc, rst=rst_pin, baudrate=spi_baud, rotation=270)
print(display.read(command=0x04, count=4))

# Set backlight brightness out of 65535
# Turn off until first image is written to work around not having RST
led = pwmio.PWMOut(led_pin, frequency=5000, duty_cycle=0)
led.duty_cycle = 0

# Start bit=1, A[2:0], 12-bit=0, differential=0, power down when done=00
XPT2046_CMD_X = 0b11010000  # X=101
XPT2046_CMD_Y = 0b10010000  # Y=001
_cal = (300, 400, 3600, 3850) # Top-left and bottom-right coordinates as raw ADC output
_max_dist = 0.05 * ((_cal[3] - _cal[1]) ** 2 + (_cal[2] - _cal[0]) ** 2) ** 0.5
tx_buf = bytearray(5)
rx_buf = bytearray(5)
tx_buf[0] = XPT2046_CMD_X
tx_buf[2] = XPT2046_CMD_Y
def read_xy():
  # Try to access SPI, wait if someone else (i.e. screen) is busy
  while not spi.try_lock():
    pass
  spi.configure(baudrate=int(2.5*10**6))
  touch_cs.value = False
  spi.write_readinto(tx_buf, rx_buf)
  touch_cs.value = True
  spi.configure(baudrate=spi_baud)
  spi.unlock()

  x = (rx_buf[1] << 5) | (rx_buf[2] >> 3)
  y = (rx_buf[3] << 5) | (rx_buf[4] >> 3)
  return (x,y)

def touch_callback(pin_num):
  global _active_screen

  # Mask the interrupt since reading the position generates a false interrupt
  GPIO.remove_event_detect(t_irq_pin.id)

  # Average 16 values
  x_raw_list = []
  y_raw_list = []
  for i in range(16):
    x, y = read_xy()
    #x_sum += x
    #y_sum += y
    if (x >= _cal[0] and y <= _cal[3]): # Valid touch
      x_raw_list.append(x)
      y_raw_list.append(y)
    #print(f'Read point at x: {x}, y: {y}')
  valid_count = len(x_raw_list)
  if valid_count > 0:
    x_raw_mean = round(sum(x_raw_list) / valid_count)
    y_raw_mean = round(sum(y_raw_list) / valid_count)
    x_raw_keep = []
    y_raw_keep = []
    for i in range(valid_count):
      dist = ((x_raw_list[i] - x_raw_mean) ** 2 + (y_raw_list[i] - y_raw_mean) ** 2) ** 0.5
      if dist < _max_dist:
        x_raw_keep.append(x_raw_list[i])
        y_raw_keep.append(y_raw_list[i])
    inlier_count = len(x_raw_keep)
    if inlier_count >= 4: # At least a quarter of the points were valid and inliers
      x_raw = sorted(x_raw_keep)[inlier_count//2]
      y_raw = sorted(y_raw_keep)[inlier_count//2]

      # Use calibration to scale to the range [0,1]
      x_cal = min(max((float(x_raw) - _cal[0]) / (_cal[2] - _cal[0]), 0), 1)
      y_cal = min(max((float(y_raw) - _cal[1]) / (_cal[3] - _cal[1]), 0), 1)

      # Swap x and y and reverse to account for screen rotation
      y = round(height*(1 - x_cal))
      x = round(width*(1 - y_cal))
      #print(f'Touch at {x},{y} [Raw: {x_raw},{y_raw}]')
      if x > (3*width/4):
        _active_screen = (_active_screen + 1) % NUM_SCREENS
      if x < (width/4):
        _active_screen = (_active_screen - 1) % NUM_SCREENS
  #  else:
  #    print(f'Not enough inliers: {inlier_count} of 16')
  #else:
  #  print(f'No valid points')

  try:
    GPIO.add_event_detect(t_irq_pin.id, GPIO.FALLING, callback=touch_callback)
  except RuntimeError as e:
    print(e)

# Get touch events
GPIO.setup(t_irq_pin.id, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(t_irq_pin.id, GPIO.FALLING, callback=touch_callback)

# Load image and convert to RGB
logo = Image.open('micronova-320x240.png').convert('RGB')
display.image(logo)

# Turn on display backlight now that an image is loaded
led.duty_cycle = 16000

# Get fonts
fontname = 'DejaVuSansMono'
try:
  font = ImageFont.truetype(fontname, 14)
  small_font = ImageFont.truetype(fontname, 10)
except:
  print('Failed to load font')

# Create a blank image for drawing.
# Swap height/width to rotate it to landscape
# TODO: Reduce size if possible to save CPU time
height = display.width
width = display.height
image = Image.new('RGB', (width, height)) # Fill entire screen with drawing space
draw = ImageDraw.Draw(image)

if profile:
  pr = cProfile.Profile()
  pr.enable()

frame_num = 0
frame_times = []
cpu_load = []
while frame_num < 10:
  start = time.time()

  if _active_screen == 0:
    # Get AmpliPi status
    sources, zones = get_amplipi_data()

    # Get stats
    try:
      ip_str = ni.ifaddresses(iface_name)[ni.AF_INET][0]['addr'] + ', ' + socket.gethostname() + '.local'
    except:
      ip_str = 'Disconnected'

    cpu_pcnt = psutil.cpu_percent()
    cpu_temp = psutil.sensors_temperatures()['cpu_thermal'][0].current
    cpu_str1 = f'{cpu_pcnt:4.1f}%'
    cpu_str2 = f'{cpu_temp:4.1f}\xb0C'
    cpu_load.append(cpu_pcnt)

    ram_total = int(psutil.virtual_memory().total / (1024*1024))
    ram_used  = int(psutil.virtual_memory().used / (1024*1024))
    ram_pcnt = 100 * ram_used / ram_total
    ram_str1 = f'{ram_pcnt:4.1f}%'
    ram_str2 = f'{ram_used}/{ram_total} MB'

    disk_usage  = psutil.disk_usage('/')
    disk_pcnt = disk_usage.percent
    disk_used = disk_usage.used / (1024**3)
    disk_total = disk_usage.total / (1024**3)
    disk_str1 = f'{disk_pcnt:4.1f}%'
    disk_str2 = f'{disk_used:.2f}/{disk_total:.2f} GB'

    # Render text
    cw = 8    # Character width
    ch = 16   # Character height
    draw.rectangle((0, 0, width-1, height-1), fill=0) # Clear image
    draw.text((0*cw, 0*ch), 'IP:',      font=font, fill='#FFFFFF')
    draw.text((0*cw, 1*ch), 'CPU:',     font=font, fill='#FFFFFF')
    draw.text((0*cw, 2*ch), 'Mem:',     font=font, fill='#FFFFFF')
    draw.text((0*cw, 3*ch), 'Disk:',    font=font, fill='#FFFFFF')
    draw.text((0*cw, int(4.5*ch)), 'Source 1:',font=font, fill='#FFFFFF')
    draw.text((0*cw, int(5.5*ch)), 'Source 2:',font=font, fill='#FFFFFF')
    draw.text((0*cw, int(6.5*ch)), 'Source 3:',font=font, fill='#FFFFFF')
    draw.text((0*cw, int(7.5*ch)), 'Source 4:',font=font, fill='#FFFFFF')

    draw.text((6*cw, 0*ch), ip_str,     font=font, fill='#FFFFFF')
    draw.text((6*cw, 1*ch), cpu_str1,   font=font, fill=gradient(cpu_pcnt))
    draw.text((6*cw, 2*ch), ram_str1,   font=font, fill=gradient(ram_pcnt))
    draw.text((6*cw, 3*ch), disk_str1,  font=font, fill=gradient(disk_pcnt))

    # BCM2837B0 is rated for [-40, 85] C
    # For now show green for anything below room temp
    draw.text((13*cw, 1*ch), cpu_str2,  font=font, fill=gradient(cpu_temp, min_val=20, max_val=85))
    draw.text((13*cw, 2*ch), ram_str2,  font=font, fill='#FFFFFF')
    draw.text((13*cw, 3*ch), disk_str2, font=font, fill='#FFFFFF')

    # Show source input names
    xs = 10*cw
    xp = xs - round(0.5*cw) # Shift playing arrow back a bit
    ys = 4*ch + round(0.5*ch)
    draw.line(((0, ys-3), (width-1, ys-3)), width=2, fill='#999999')
    for i in range(4):
      if sources[i]['playing']:
        draw.polygon([(xp, ys + i*ch + 3), (xp + cw-3, ys + round((i+0.5)*ch)), (xp, ys + (i+1)*ch - 3)], fill='#28a745')
      draw.text((xs + 1*cw, ys + i*ch), sources[i]['name'], font=font, fill='#F0E68C')
    draw.line(((0, ys+4*ch+2), (width-1, ys+4*ch+2)), width=2, fill='#999999')

    # Show volumes
    draw_volume_bars(draw, font, small_font, zones, y=9*ch, height=height-9*ch)

    # Send the updated image to the display
    display.image(image)
  elif _active_screen == 1:
    display.image(logo)
  elif _active_screen == 2:
    draw.rectangle((0, 0, width-1, height-1), fill='#FF0000')
    display.image(image)

  end = time.time()
  frame_times.append(end - start)
  #print(f'frame time: {sum(frame_times)/len(frame_times):.3f}s, {sum(cpu_load)/len(cpu_load):.1f}%')
  sleep_time = update_period - (end-start)
  if sleep_time > 0:
    time.sleep(sleep_time)
  else:
    print('Warning: frame took too long')

  if profile:
    frame_num = frame_num + 1

if profile:
  pr.disable()
  pr.print_stats(sort='time')
