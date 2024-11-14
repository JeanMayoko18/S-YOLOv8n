import math
import os
import random

import cv2
import numpy as np
import torch
from PIL import Image
from torch.utils import data

from pathlib import Path  # Import Path from pathlib
#from utils.util import *

FORMATS = 'bmp', 'dng', 'jpeg', 'jpg', 'mpo', 'png', 'tif', 'tiff', 'webp'


class NewDataset(data.Dataset):

    def __init__(self,  input_size, params, augment, filenames):
        super(NewDataset, self).__init__() #Initialize parent class
        self.params = params
        self.mosaic = augment
        self.augment = augment
        self.input_size = input_size
        # Initialize label_files with the filenames provided
        self.newfilenames = []
        self.label_files = []
        #######################################################################################################################################
        # Add debugging to check label file validity
        for filename in filenames:
            label_path = filename.replace('.jpg', '.txt').replace('.png', '.txt').replace('.jpeg', '.txt').replace('images', 'labels')
            if os.path.exists(label_path):
                try:
                    with open(label_path, 'r') as f:
                        lines = f.readlines()
                        for line in lines:
                            values = line.strip().split()
                            if len(values) != 5:
                                print(f"Warning: Invalid line in label file: {label_path}, Line: {line.strip()}")
                                break  # Stop checking if invalid line found
                        else:  # This block executes if the loop completes without a break
                            self.newfilenames.append(filename)  # Add filename if label file is valid
                except Exception as e:
                    print(f"Error reading label file: {label_path}, Error: {e}")
            else:
                print(f"Warning: Label file not found: {label_path}")
        for filename in self.newfilenames:
            label_path = filename.replace('.jpg', '.txt').replace('.png', '.txt').replace('.jpeg', '.txt').replace('images', 'labels')
            self.label_files.append(label_path)  # Add the label file path to the list
        ###########################################################################################################################################
        # Read labels
        labels = self.load_label(self.newfilenames)
        self.labels = list(labels.values())
        self.filenames = list(labels.keys())  # update
        self.n = len(self.newfilenames)  # number of samples
        self.indices = range(self.n)
        # Albumentations (optional, only used if package is installed)
        self.albumentations = Albumentations()

   ################################################################PRINCIPALS METHODS########################################################################33
    def __getitem__(self, index):
        index = self.indices[index]

        params = self.params
        mosaic = self.mosaic and random.random() < params['mosaic']

        if mosaic:
            # Load MOSAIC
            image, label = self.load_mosaic(index, params)
            # Convert label to NumPy array
            label = np.array(label)
            # MixUp augmentation
            if random.random() < params['mix_up']:
                index = random.choice(self.indices)
                mix_image1, mix_label1 = image, label
                mix_image2, mix_label2 = self.load_mosaic(index, params)

                image, label = self.mix_up(mix_image1, mix_label1, mix_image2, mix_label2)
        else:
            # Load image
            image, shape = self.load_image(index)
            h, w = image.shape[:2]

            # Resize
            image, ratio, pad = self.resize(image, self.input_size, self.augment)

            label = self.labels[index].copy()
            if label.size:
                label[:, 1:] = self.wh2xy(label[:, 1:], ratio[0] * w, ratio[1] * h, pad[0], pad[1])
            if self.augment:
                image, label = self.random_perspective(image, label, params)

        nl = len(label)  # number of labels
        h, w = image.shape[:2]
        cls = label[:, 0:1]
        box = label[:, 1:5]
        box = self.xy2wh(box, w, h)

        if self.augment:
            # Albumentations
            image, box, cls = self.albumentations(image, box, cls)
            nl = len(box)  # update after albumentations
            # HSV color-space
            self.augment_hsv(image, params)
            # Flip up-down
            if random.random() < params['flip_ud']:
                image = np.flipud(image)
                if nl:
                    box[:, 1] = 1 - box[:, 1]
            # Flip left-right
            if random.random() < params['flip_lr']:
                image = np.fliplr(image)
                if nl:
                    box[:, 0] = 1 - box[:, 0]

        target_cls = torch.zeros((nl, 1))
        target_box = torch.zeros((nl, 4))
        if nl:
            target_cls = torch.from_numpy(cls)
            target_box = torch.from_numpy(box)

        # Convert HWC to CHW, BGR to RGB
        sample = image.transpose((2, 0, 1))[::-1]
        sample = np.ascontiguousarray(sample)

        return torch.from_numpy(sample), target_cls, target_box, torch.zeros(nl)

    def __len__(self):
        return len(self.filenames)

    def load_image(self, i):
        image = cv2.imread(self.filenames[i])
        h, w = image.shape[:2]
        r = self.input_size / max(h, w)
        def resample():
          """Randomly select an interpolation method."""
          choices = [cv2.INTER_NEAREST, cv2.INTER_LINEAR, cv2.INTER_AREA,
                    cv2.INTER_CUBIC, cv2.INTER_LANCZOS4]
          return random.choice(choices)

        if r != 1:
            image = cv2.resize(image,
                               dsize=(int(w * r), int(h * r)),
                               interpolation=resample() if self.augment else cv2.INTER_LINEAR)
        return image, (h, w)

    def load_mosaic(self, index, params):
        label4 = []

        border = [-self.input_size // 2, -self.input_size // 2]
        image4 = np.full((self.input_size * 2, self.input_size * 2, 3), 0, dtype=np.uint8)
        y1a, y2a, x1a, x2a, y1b, y2b, x1b, x2b = (None, None, None, None, None, None, None, None)

        xc = int(random.uniform(-border[0], 2 * self.input_size + border[1]))
        yc = int(random.uniform(-border[0], 2 * self.input_size + border[1]))

        indices = [index] + random.choices(self.indices, k=3)
        random.shuffle(indices)


        for i, index in enumerate(indices):
            # Load image
            image, _ = self.load_image(index)
            shape = image.shape
            if i == 0:  # top left
                x1a = max(xc - shape[1], 0)
                y1a = max(yc - shape[0], 0)
                x2a = xc
                y2a = yc
                x1b = shape[1] - (x2a - x1a)
                y1b = shape[0] - (y2a - y1a)
                x2b = shape[1]
                y2b = shape[0]
            if i == 1:  # top right
                x1a = xc
                y1a = max(yc - shape[0], 0)
                x2a = min(xc + shape[1], self.input_size * 2)
                y2a = yc
                x1b = 0
                y1b = shape[0] - (y2a - y1a)
                x2b = min(shape[1], x2a - x1a)
                y2b = shape[0]
            if i == 2:  # bottom left
                x1a = max(xc - shape[1], 0)
                y1a = yc
                x2a = xc
                y2a = min(self.input_size * 2, yc + shape[0])
                x1b = shape[1] - (x2a - x1a)
                y1b = 0
                x2b = shape[1]
                y2b = min(y2a - y1a, shape[0])
            if i == 3:  # bottom right
                x1a = xc
                y1a = yc
                x2a = min(xc + shape[1], self.input_size * 2)
                y2a = min(self.input_size * 2, yc + shape[0])
                x1b = 0
                y1b = 0
                x2b = min(shape[1], x2a - x1a)
                y2b = min(y2a - y1a, shape[0])

            pad_w = x1a - x1b
            pad_h = y1a - y1b
            image4[y1a:y2a, x1a:x2a] = image[y1b:y2b, x1b:x2b]

            # Labels
            label = self.labels[index].copy()
            if len(label):
                label[:, 1:] = self.wh2xy(label[:, 1:], shape[1], shape[0], pad_w, pad_h)
            label4.append(label)

        # Concat/clip labels
        label4 = np.concatenate(label4, 0)
        for x in label4[:, 1:]:
            np.clip(x, 0, 2 * self.input_size, out=x)

        # Augment
        image4, label4 = self.random_perspective(image4, label4, params, border)

        return image4, label4

    @staticmethod
    def collate_fn(batch):
        samples, cls, box, indices = zip(*batch)

        cls = torch.cat(cls, dim=0)
        box = torch.cat(box, dim=0)

        new_indices = list(indices)
        for i in range(len(indices)):
            new_indices[i] += i
        indices = torch.cat(new_indices, dim=0)

        targets = {'cls': cls,
                   'box': box,
                   'idx': indices}
        return torch.stack(samples, dim=0), targets

    def load_labels(self, filenames):

      path = str(Path(self.label_files[0]).parent)  # path to labels
      labels = {}
      # Create an empty list to store label data
      all_label_data = []
      # Iterate through each filename
      for filename in filenames:
          # Extract the base filename (without extension)
          path = os.path.dirname(filename)
          # Extract filename without extension for labels
          txt_file_name = Path(filename).stem
          # Construct the full path to the label file using the filename without extension
          label_file_path = os.path.join(path.replace('images', 'labels'), f"{txt_file_name}.txt")
           # Check if label file exists
          if os.path.exists(label_file_path):
              with open(label_file_path, 'r') as f:
                label_data = [x.split() for x in f.read().strip().splitlines() if len(x)]
                # Append the label data to the all_label_data list
                all_label_data.extend(label_data)
                # Add the filename and its corresponding label data to the dictionary
                labels[filename] = label_data
          else:
             print(f"Warning: Label file not found: {label_file_path}")  # Print a warning

      # Check for invalid label files
      if any(len(label_data) > 5 for label_data in all_label_data):
          print("Warning: Invalid label files found. Ensure all files have 5 values per line (class, x_center, y_center, width, height).")
          return {} # return an empty dictionary if there is wrong format

      # Convert the label data into a NumPy array if the format is valid
      try:
          # Convert the all_label_data list into a NumPy array with float32 type
          label_array = np.array(all_label_data, dtype=np.float32)
      except ValueError as e:
          # Handle the exception, such as printing an error message and exiting
          print(f"Error creating label array: {e}")
          print("Check for inconsistencies in the label files.")
          return {} # return an empty dictionary in case of error

      return labels # return dictionary with filenames and their corresponding label data.

    @staticmethod
    def load_label(filenames):
        path = f'{os.path.dirname(filenames[0])}.cache'
        if os.path.exists(path):
            return torch.load(path)
        x = {}
        for filename in filenames:
            try:
                # verify images
                with open(filename, 'rb') as f:
                    image = Image.open(f)
                    image.verify()  # PIL verify
                shape = image.size  # image size
                assert (shape[0] > 9) & (shape[1] > 9), f'image size {shape} <10 pixels'
                assert image.format.lower() in FORMATS, f'invalid image format {image.format}'

                # verify labels
                a = f'{os.sep}images{os.sep}'
                b = f'{os.sep}labels{os.sep}'
                if os.path.isfile(b.join(filename.rsplit(a, 1)).rsplit('.', 1)[0] + '.txt'):
                    with open(b.join(filename.rsplit(a, 1)).rsplit('.', 1)[0] + '.txt') as f:
                        label = [x.split() for x in f.read().strip().splitlines() if len(x)]

                        label = np.array(label, dtype=np.float32)
                    nl = len(label)
                    if nl:
                        assert (label >= 0).all()
                        assert label.shape[1] == 5
                        assert (label[:, 1:] <= 1).all()
                        _, i = np.unique(label, axis=0, return_index=True)
                        if len(i) < nl:  # duplicate row check
                            label = label[i]  # remove duplicates
                    else:
                        label = np.zeros((0, 5), dtype=np.float32)
                else:
                    label = np.zeros((0, 5), dtype=np.float32)
            except FileNotFoundError:
                label = np.zeros((0, 5), dtype=np.float32)
            except AssertionError:
                continue
            x[filename] = label
        torch.save(x, path)
        return x


 ############################################################OTHER METHODS##############################################################################
    def wh2xy(self, x, w=640, h=640, padw=0, padh=0):
          # Convert nx4 boxes from [x, y, w, h] normalized to [x1, y1, x2, y2] where xy1=top-left, xy2=bottom-right
          y = x.clone() if isinstance(x, torch.Tensor) else x.copy()
          y[:, 0] = w * (x[:, 0] - x[:, 2] / 2) + padw  # top left x
          y[:, 1] = h * (x[:, 1] - x[:, 3] / 2) + padh  # top left y
          y[:, 2] = w * (x[:, 0] + x[:, 2] / 2) + padw  # bottom right x
          y[:, 3] = h * (x[:, 1] + x[:, 3] / 2) + padh  # bottom right y
          return y

    def xy2wh(self,x, w, h):
        # warning: inplace clip
        x[:, [0, 2]] = x[:, [0, 2]].clip(0, w - 1E-3)  # x1, x2
        x[:, [1, 3]] = x[:, [1, 3]].clip(0, h - 1E-3)  # y1, y2

        # Convert nx4 boxes
        # from [x1, y1, x2, y2] to [x, y, w, h] normalized where xy1=top-left, xy2=bottom-right
        y = np.copy(x)
        y[:, 0] = ((x[:, 0] + x[:, 2]) / 2) / w  # x center
        y[:, 1] = ((x[:, 1] + x[:, 3]) / 2) / h  # y center
        y[:, 2] = (x[:, 2] - x[:, 0]) / w  # width
        y[:, 3] = (x[:, 3] - x[:, 1]) / h  # height
        return y


    def resample():
        choices = (cv2.INTER_AREA,
                   cv2.INTER_CUBIC,
                   cv2.INTER_LINEAR,
                   cv2.INTER_NEAREST,
                   cv2.INTER_LANCZOS4)
        return random.choice(seq=choices)


    def augment_hsv(self,image, params):
        # HSV color-space augmentation
        h = params['hsv_h']
        s = params['hsv_s']
        v = params['hsv_v']

        r = np.random.uniform(-1, 1, 3) * [h, s, v] + 1
        h, s, v = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))

        x = np.arange(0, 256, dtype=r.dtype)
        lut_h = ((x * r[0]) % 180).astype('uint8')
        lut_s = np.clip(x * r[1], 0, 255).astype('uint8')
        lut_v = np.clip(x * r[2], 0, 255).astype('uint8')

        hsv = cv2.merge((cv2.LUT(h, lut_h), cv2.LUT(s, lut_s), cv2.LUT(v, lut_v)))
        cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR, dst=image)  # no return needed


    def resize(self,image, input_size, augment):
        # Resize and pad image while meeting stride-multiple constraints
        shape = image.shape[:2]  # current shape [height, width]

        # Scale ratio (new / old)
        r = min(input_size / shape[0], input_size / shape[1])
        if not augment:  # only scale down, do not scale up (for better val mAP)
            r = min(r, 1.0)

        # Compute padding
        pad = int(round(shape[1] * r)), int(round(shape[0] * r))
        w = (input_size - pad[0]) / 2
        h = (input_size - pad[1]) / 2

        if shape[::-1] != pad:  # resize
            image = cv2.resize(image,
                               dsize=pad,
                               interpolation = self.resample() if augment else cv2.INTER_LINEAR)
        top, bottom = int(round(h - 0.1)), int(round(h + 0.1))
        left, right = int(round(w - 0.1)), int(round(w + 0.1))
        image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_CONSTANT)  # add border
        return image, (r, r), (w, h)


    def candidates(self,box1, box2):
        # box1(4,n), box2(4,n)
        w1, h1 = box1[2] - box1[0], box1[3] - box1[1]
        w2, h2 = box2[2] - box2[0], box2[3] - box2[1]
        aspect_ratio = np.maximum(w2 / (h2 + 1e-16), h2 / (w2 + 1e-16))  # aspect ratio
        return (w2 > 2) & (h2 > 2) & (w2 * h2 / (w1 * h1 + 1e-16) > 0.1) & (aspect_ratio < 100)


    def random_perspective(self,image, label, params, degrees=10, translate=.1, scale=.1, shear=10, perspective=0.0, border=(0, 0)):
        h = image.shape[0] + border[0] * 2
        w = image.shape[1] + border[1] * 2

        # Center
        center = np.eye(3)
        center[0, 2] = -image.shape[1] / 2  # x translation (pixels)
        center[1, 2] = -image.shape[0] / 2  # y translation (pixels)

        # Perspective
        perspective = np.eye(3)

        # Rotation and Scale
        rotate = np.eye(3)
        a = random.uniform(-params['degrees'], params['degrees'])
        s = random.uniform(1 - params['scale'], 1 + params['scale'])
        rotate[:2] = cv2.getRotationMatrix2D(angle=a, center=(0, 0), scale=s)

        # Shear
        shear = np.eye(3)
        shear[0, 1] = math.tan(random.uniform(-params['shear'], params['shear']) * math.pi / 180)
        shear[1, 0] = math.tan(random.uniform(-params['shear'], params['shear']) * math.pi / 180)

        # Translation
        translate = np.eye(3)
        translate[0, 2] = random.uniform(0.5 - params['translate'], 0.5 + params['translate']) * w
        translate[1, 2] = random.uniform(0.5 - params['translate'], 0.5 + params['translate']) * h

        # Combined rotation matrix, order of operations (right to left) is IMPORTANT
        matrix = translate @ shear @ rotate @ perspective @ center
        if (border[0] != 0) or (border[1] != 0) or (matrix != np.eye(3)).any():  # image changed
            image = cv2.warpAffine(image, matrix[:2], dsize=(w, h), borderValue=(0, 0, 0))

        # Transform label coordinates
        n = len(label)
        if n:
            xy = np.ones((n * 4, 3))
            xy[:, :2] = label[:, [1, 2, 3, 4, 1, 4, 3, 2]].reshape(n * 4, 2)  # x1y1, x2y2, x1y2, x2y1
            xy = xy @ matrix.T  # transform
            xy = xy[:, :2].reshape(n, 8)  # perspective rescale or affine

            # create new boxes
            x = xy[:, [0, 2, 4, 6]]
            y = xy[:, [1, 3, 5, 7]]
            box = np.concatenate((x.min(1), y.min(1), x.max(1), y.max(1))).reshape(4, n).T

            # clip
            box[:, [0, 2]] = box[:, [0, 2]].clip(0, w)
            box[:, [1, 3]] = box[:, [1, 3]].clip(0, h)
            # filter candidates
            indices = self.candidates(box1=label[:, 1:5].T * s, box2=box.T)

            label = label[indices]
            label[:, 1:5] = box[indices]

        return image, label

   #def random_perspective(self, img, targets=(), degrees=10, translate=.1, scale=.1, shear=10, perspective=0.0, border=(0, 0)):
            # targets = [cls, xyxy]

        #return img, targets

    def mix_up(self,image1, label1, image2, label2):
        # Applies MixUp augmentation https://arxiv.org/pdf/1710.09412.pdf
        alpha = np.random.beta(a=32.0, b=32.0)  # mix-up ratio, alpha=beta=32.0
        image = (image1 * alpha + image2 * (1 - alpha)).astype(np.uint8)
        label = np.concatenate((label1, label2), 0)
        return image, label



class Albumentations:
    def __init__(self):
        self.transform = None
        try:
            import albumentations as A
            from albumentations.pytorch import ToTensorV2
            # Define the augmentation pipeline with an image and bbox transformation.
            transforms = [A.Blur(p=0.01),
                          A.CLAHE(p=0.01),
                          A.ToGray(p=0.01),
                          A.MedianBlur(p=0.01),
                           # ... other transformations
                          A.Resize(width=640, height=640),
                          A.HorizontalFlip(p=0.5),
                          A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
                          A.pytorch.ToTensorV2(),
                          
                          ]
            self.transform = A.Compose(transforms,
                                                    A.BboxParams('yolo', ['class_labels']))
            
        except ImportError:  # package not installed, skip
            pass

    def __call__(self, image, box, cls):
        if self.transform:
            x = self.transform(image=image,
                               bboxes=box,
                               class_labels=cls)
            image = x['image']
            box = np.array(x['bboxes'])
            cls = np.array(x['class_labels'])
        return image, box, cls