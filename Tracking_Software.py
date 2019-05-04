#!/usr/bin/env python3

__author__ = "Gavin Niendorf"
__email__ = "gavinniendorf@gmail.com"

import cv2
import sys
import os.path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from tqdm import tqdm
from optparse import OptionParser
from scipy.spatial import distance
from scipy.ndimage import measurements
from scipy.ndimage.measurements import label

parser = OptionParser()
parser.add_option('--fb', action="store", type="int", dest="framebuff", help="frames a track has to be in view to count", default=1)
parser.add_option('--border', action="store", dest="border", help="border cropped out", default=[30, 30])
parser.add_option('--meanshift', action="store", type="int", dest="meanshift", help="maximum mean shift per frame", default=25)
parser.add_option('--denoise', action="store", type="int", dest="denoise_thresh", help="denoise threshold", default=500)
parser.add_option('--convert', action="store", type="int", dest="convert", help="pixels per cm conversion", default=120)
parser.add_option('--video', action="store", type="string", dest="video", help="video file name", default="1hour.mp4")
parser.add_option('--output', action="store", type="string", dest="output", help="output file name", default="tracks.csv")
opt, args = parser.parse_args()

def denoise(image, nthresh):
    """Return the signal that is above nthresh"""
    labeled_array, num_features = label(image)
    binc = np.bincount(labeled_array.ravel())
    noise_idx = np.where(binc <= nthresh)
    mask = np.in1d(labeled_array, noise_idx).reshape(np.shape(image))
    image[mask] = 0
    return image

def get_length(idx_lists):
    """Return the (linear) length of a track"""
    start = [idx_lists[0][0], idx_lists[1][0]]
    end = [idx_lists[0][-1], idx_lists[1][-1]]
    dis = distance.euclidean(start, end)
    return dis

def get_area(idx_lists):
    """Return the area of a track"""
    binc = np.bincount(idx_lists.ravel())
    return binc

def train_bkg(cap, nframes, fgbg, border):
    """Return the trained background remover"""
    for i in np.arange(nframes):
        ret, frame = cap.read()
        frame = frame[border[1]:-border[1], border[0]:-border[0]]
        fgbg.apply(frame)
    return fgbg

def remove_border_tracks(image):
    """Return image with border tracks removed"""
    lb = image[:,0][np.nonzero(image[:, 0])]
    rb = image[:,-1][np.nonzero(image[:, -1])]
    tb = image[0, 1:-1][np.nonzero(image[0, 1:-1])]
    bb = image[-1, 1:-1][np.nonzero(image[-1, 1:-1])]
    for val1 in lb:
        image[image == val1] = 0
    for val2 in rb:
        image[image == val2] = 0
    for val3 in tb:
        image[image == val3] = 0
    for val4 in bb:
        image[image == val4] = 0  
    return image 

framebuff = opt.framebuff
border = opt.border
meanshift_length = opt.meanshift
denoise_thresh = opt.denoise_thresh
pix_per_cm = opt.convert
video = opt.video

if not os.path.isfile(video):
    sys.exit("Exception: file %s does not exist!" % video)

print("Loading video from file")
cap = cv2.VideoCapture(opt.video)

print("Training background remover")
fgbg = cv2.createBackgroundSubtractorMOG2(detectShadows=False) #Initialize background remover
fgbg = train_bkg(cap, 1000, fgbg, border) #Train background remover

len_abs = []
area_abs = []
frames_abs = []

len_buffer = []
area_buffer = []
frame_buffer = []
mean_buffer = []

seen = []

current_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
final_frame = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))-1

if final_frame <= 0:
    sys.exit("Exception: video has no frames")

print("Starting analysis")
for tick in tqdm(np.arange(final_frame-current_frame)):
    _, frame = cap.read() #Image frame
    frame = frame[border[1]:-border[1], border[0]:-border[0]] #Cropped frame

    #Remove background from image
    mask = fgbg.apply(frame)

    #Remove signal below threshold
    mask_proc = denoise(mask, denoise_thresh) #De-noised image
    if np.all(mask_proc == 0): #Continue if no tracks left
        continue

    #Cluster tracks
    clusters, num = measurements.label(mask_proc, structure=np.ones((3,3)))
    labels=np.arange(1, num+1)
    
    #Remove tracks on border of image.
    clusters = remove_border_tracks(clusters)
    if np.all(clusters == 0): #Continue if no tracks left
        continue
    
    #Identify the tracks across frames
    seen[:len(seen)] = [0] * len(seen)
    for lab in labels:
        seen_flag = 0
        idxs = np.where(clusters == lab)
        if np.shape(idxs)[1]==0:
            continue
        mean = [np.mean(idxs[1]), np.mean(idxs[0])]
        length = get_length(idxs)
        area = get_area(clusters)[lab]
        #Try to identify if this track was seen last frame
        for idx, val in enumerate(mean_buffer):
            dis = distance.euclidean(val, mean)
            if dis < meanshift_length:
                seen_flag=1
                frame_buffer[idx] += 1
                seen[idx] = 1
                mean_buffer[idx] = mean
                #Only add to buffer the maximum for each track
                if length > len_buffer[idx]:
                    len_buffer[idx] = length
                if area > area_buffer[idx]:
                    area_buffer[idx] = area
        #If a track is new, add it to buffer lists
        if not seen_flag:
            seen.append(1)
            frame_buffer.append(1)
            mean_buffer.append(mean)
            len_buffer.append(length)
            area_buffer.append(area)

    #Selects tracks which disapeered from the last frame.
    #Need to reverse idx order since we remove by index.
    for idx in np.where(np.array(seen) == 0)[0][::-1]:
        #If a track was in view above the threshold append it to the abs lists
        if frame_buffer[idx] > framebuff:
            frames_abs.append(frame_buffer[idx])
            len_abs.append(len_buffer[idx]/(pix_per_cm))
            area_abs.append(area_buffer[idx]/(pix_per_cm**2))
        #Delete a track from the buffer when it disapeers
        del len_buffer[idx]
        del seen[idx]
        del mean_buffer[idx]
        del area_buffer[idx]
        del frame_buffer[idx]

data = pd.DataFrame({"Lengths":len_abs, "Areas":area_abs, "Frames":frames_abs, "Video":video})
data.to_csv(opt.output, index=False)

