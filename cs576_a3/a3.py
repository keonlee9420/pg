root = './'
import sys
sys.path.append(root)

import warnings
warnings.filterwarnings("ignore")

import os
import cv2
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable
from torch.utils.data import DataLoader
from torchvision import transforms

from data import VOCDetection
def makedirs(path):
    if not os.path.exists(path):
        os.makedirs(path)

# Configurations
run_name = 'vgg16'          # experiment name.
ckpt_root = 'checkpoints'   # from/to which directory to load/save checkpoints.
data_root = 'dataset'       # where the data exists.
pretrained_backbone_path = 'weights/vgg_features.pth'

device = 'cuda' if torch.cuda.is_available() else 'cpu'
lr = 0.001          # learning rate
batch_size = 64     # batch_size
last_epoch = 1      # the last training epoch. (defulat: 1)
max_epoch = 200     # maximum epoch for the training.

num_boxes = 2       # the number of boxes for each grid in Yolo v1.
num_classes = 20    # the number of classes in Pascal VOC Detection.
grid_size = 7       # 3x224x224 image is reduced to (5*num_boxes+num_classes)x7x7.
lambda_coord = 7    # weight for coordinate regression loss.
lambda_noobj = 0.5  # weight for no-objectness confidence loss.

ckpt_dir = os.path.join(root, ckpt_root)
makedirs(ckpt_dir)

train_dset = VOCDetection(root=data_root, split='train')
train_dloader = DataLoader(train_dset, batch_size=batch_size, shuffle=True, drop_last=True, num_workers=8)

test_dset = VOCDetection(root=data_root, split='test')
test_dloader = DataLoader(test_dset, batch_size=batch_size, shuffle=False, drop_last=False, num_workers=8)


# Problem 1. Implement Architecture
class Yolo(nn.Module):
    def __init__(self, grid_size, num_boxes, num_classes):
        super(Yolo, self).__init__()
        self.S = grid_size
        self.B = num_boxes
        self.C = num_classes
        self.features = nn.Sequential(
            # implement backbone network here.
            nn.Conv2d(3, 64, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 64, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(64, 128, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, 128, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(128, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(256, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
        )
        self.detector = nn.Sequential(
            # implement detection head here.
            nn.Linear(in_features=25088, out_features=4096, bias=True),
            nn.ReLU(inplace=True),
            nn.Dropout(p=.5, inplace=True),
            nn.Linear(in_features=4096, out_features=1470, bias=True)
        )
    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.detector(x)
        x = F.sigmoid(x)
        x = x.view(-1, self.S, self.S, self.B*5+self.C)
        return x

model = Yolo(grid_size, num_boxes, num_classes)
model = model.to(device)
pretrained_weights = torch.load(pretrained_backbone_path)
model.load_state_dict(pretrained_weights)
# It should print out <All keys matched successfully> when you implemented VGG correctly.

# Freeze the backbone network.
model.features.requires_grad_(False)
model_params = [v for v in model.parameters() if v.requires_grad is True]
optimizer = optim.SGD(model_params, lr=lr, momentum=0.9, weight_decay=5e-4)

# Load the last checkpoint if exits.
ckpt_path = os.path.join(ckpt_dir, 'last.pth') 

if os.path.exists(ckpt_path): 
    ckpt = torch.load(ckpt_path)
    model.load_state_dict(ckpt['model'])
    optimizer.load_state_dict(ckpt['optimizer'])
    last_epoch = ckpt['epoch'] + 1
    print('Last checkpoint is loaded. start_epoch:', last_epoch)
else:
    print('No checkpoint is found.')


# Problem 2. Implement Architecture
class Loss(nn.Module):
    def __init__(self, grid_size=7, num_bboxes=2, num_classes=20):
        """ Loss module for Yolo v1.
        Use grid_size, num_bboxes, num_classes information if necessary.

        Args:
            grid_size: (int) size of input grid.
            num_bboxes: (int) number of bboxes per each cell.
            num_classes: (int) number of the object classes.
        """
        super(Loss, self).__init__()
        self.S = grid_size
        self.B = num_bboxes
        self.C = num_classes

    def compute_iou(self, bbox1, bbox2):
        """ Compute the IoU (Intersection over Union) of two set of bboxes, each bbox format: [x1, y1, x2, y2].
        Use this function if necessary.

        Args:
            bbox1: (Tensor) bounding bboxes, sized [N, 4].
            bbox2: (Tensor) bounding bboxes, sized [M, 4].
        Returns:
            (Tensor) IoU, sized [N, M].
        """
        N = bbox1.size(0)
        M = bbox2.size(0)

        # Compute left-top coordinate of the intersections
        lt = torch.max(
            bbox1[:, :2].unsqueeze(1).expand(N, M, 2), # [N, 2] -> [N, 1, 2] -> [N, M, 2]
            bbox2[:, :2].unsqueeze(0).expand(N, M, 2)  # [M, 2] -> [1, M, 2] -> [N, M, 2]
        )
        # Conpute right-bottom coordinate of the intersections
        rb = torch.min(
            bbox1[:, 2:].unsqueeze(1).expand(N, M, 2), # [N, 2] -> [N, 1, 2] -> [N, M, 2]
            bbox2[:, 2:].unsqueeze(0).expand(N, M, 2)  # [M, 2] -> [1, M, 2] -> [N, M, 2]
        )
        # Compute area of the intersections from the coordinates
        wh = rb - lt   # width and height of the intersection, [N, M, 2]
        wh[wh < 0] = 0 # clip at 0
        inter = wh[:, :, 0] * wh[:, :, 1] # [N, M]

        # Compute area of the bboxes
        area1 = (bbox1[:, 2] - bbox1[:, 0]) * (bbox1[:, 3] - bbox1[:, 1]) # [N, ]
        area2 = (bbox2[:, 2] - bbox2[:, 0]) * (bbox2[:, 3] - bbox2[:, 1]) # [M, ]
        area1 = area1.unsqueeze(1).expand_as(inter) # [N, ] -> [N, 1] -> [N, M]
        area2 = area2.unsqueeze(0).expand_as(inter) # [M, ] -> [1, M] -> [N, M]

        # Compute IoU from the areas
        union = area1 + area2 - inter # [N, M, 2]
        iou = inter / union           # [N, M, 2]

        return iou

    def forward(self, pred_tensor, target_tensor):
        """ Compute loss.

        Args:
            pred_tensor (Tensor): predictions, sized [batch_size, S, S, Bx5+C], 5=len([x, y, w, h, conf]).
            target_tensor (Tensor):  targets, sized [batch_size, S, S, Bx5+C].
        Returns:
            loss_xy (Tensor): localization loss for center positions (x, y) of bboxes.
            loss_wh (Tensor): localization loss for width, height of bboxes.
            loss_obj (Tensor): objectness loss.
            loss_noobj (Tensor): no-objectness loss.
            loss_class (Tensor): classification loss.
        """
        # Write your code here
        def center_to_ltrb(_tensor):
            """ Transform tensor from 'center' to 'left-top, right-bottom'

            Args:
                _tensor (Tensor) : original, sized [filtered x B, 5], 5=len([x, y, w, h, conf]).
            Returns:
                tensor_ltrb (Tensor) : for computing iou, sized [filtered x B, 5] where we have 'filtered' cells, 5=len([x1, y1, x2, y2, conf]).
            """
            tensor_ltrb = torch.zeros_like(_tensor)
            tensor_ltrb[:, :2] = _tensor[:, :2] - _tensor[:, 2:4] * .5 # compute x1, y1
            tensor_ltrb[:, 2:4] = _tensor[:, :2] + _tensor[:, 2:4] * .5 # compute x2, y2
            return tensor_ltrb

        # mask for the cells which contain object
        mask_obj = target_tensor[:, :, :, 4] == 1 # [batch_size, S, S]
        mask_obj = mask_obj.unsqueeze(-1).expand_as(target_tensor) # [batch_size, S, S, Bx5+C], 5=len([x, y, w, h, conf])
        # mask for the cells which does NOT contain object
        mask_noobj = target_tensor[:, :, :, 4] == 0 # [batch_size, S, S]
        mask_noobj = mask_noobj.unsqueeze(-1).expand_as(target_tensor) # [batch_size, S, S, Bx5+C], 5=len([x, y, w, h, conf])

        # pred_tensor which contain object: '(tensor)_bb' for bounding boxes, '(tensor)_class' for calsses
        pred_tensor_obj = pred_tensor[mask_obj] # [batch_size, S, S, Bx5+C], 5=len([x, y, w, h, conf])
        pred_tensor_obj_bb = pred_tensor_obj[:, :, :, :self.B*5].view([-1, 5]) # [filtered x B, 5]
        pred_tensor_obj_class = pred_tensor_obj[:, :, :, self.B*5:].view([-1, self.C]) # [filtered, C]
        # target_tensor which contain object: '(tensor)_bb' for bounding boxes, '(tensor)_class' for calsses
        target_tensor_obj = target_tensor[mask_obj] # [batch_size, S, S, Bx5+C], 5=len([x, y, w, h, conf])
        target_tensor_obj_bb = target_tensor_obj[:, :, :, :self.B*5].view([-1, 5]) # [filtered x B, 5]
        target_tensor_obj_class = target_tensor_obj[:, :, :, self.B*5:].view([-1, self.C]) # [filtered, C]

        # mask for the bounding boxes which is resposible for the ground truth.
        mask_resp = torch.zeros_like(pred_tensor_obj_bb)

        for i in range(0, target_tensor_obj_bb.shape[0], self.B):
            # preprocess
            pred_bb = pred_tensor_obj_bb[i:i+self.B] # [B, 5], 5=len([x, y, w, h, conf])
            target_bb = target_tensor_obj_bb[i:i+self.B]
            pred_bb_ltrb = center_to_ltrb(pred_bb) # [B, 5], 5=len([x1, y1, x2, y2, conf])
            target_bb_ltrb = center_to_ltrb(target_bb)

            # compute iou
            iou = compute_iou(pred_bb_ltrb[:, :4], target_bb_ltrb[0, :4]) # [B, 1], target has duplicate ground truth as in encoder function in data.py
            bb_resp_iou, bb_resp_idx = iou.max(0) # choose maximum iou as resposible

            # update
            mask_resp[i+bb_resp_idx] = 1

        # --- compute each loss ---
        batch_size = target_tensor.shape[0]
        pred_resp = pred_tensor_obj_bb[mask_resp]
        target_resp = target_tensor_obj_bb[mask_resp]

        # 1. loss_xy
        loss_xy = torch.sum((target_resp[:, :2] - pred_resp[:, :2])**2) / batch_size

        # 2. loss_wh
        loss_wh = torch.sum((torch.sqrt(target_resp[:, 2:4]) - torch.sqrt(pred_resp[:, 2:4]))**2) / batch_size

        # 3. loss_obj
        loss_obj = torch.sum((target_resp[:, 4] - pred_resp[:, 4])**2) / batch_size

        # 4. loss_noobj
        # pred_tensor & target_tensor which does NOT contain object
        pred_tensor_noobj = pred_tensor[mask_noobj] # [batch_size, S, S, Bx5+C], 5=len([x, y, w, h, conf])
        target_tensor_noobj = target_tensor[mask_noobj]
        pred_noobj_conf = pred_tensor_noobj[:, :, :, [4, 9]].view([-1, 2]) # only consider 'conf'
        target_noobj_conf = target_tensor_noobj[:, :, :, [4, 9]].view([-1, 2])
        loss_noobj = torch.sum((target_noobj_conf - pred_noobj_conf)**2) / batch_size

        # 5. loss_class
        loss_class = torch.sum((target_tensor_obj_class - pred_tensor_obj_class)**2) / batch_size

        return loss_xy, loss_wh, loss_obj, loss_noobj, loss_class

compute_loss = Loss(grid_size, num_boxes, num_classes)


# # compute_iou testing
# def compute_iou(bbox1, bbox2):
#     """ Compute the IoU (Intersection over Union) of two set of bboxes, each bbox format: [x1, y1, x2, y2].
#     Use this function if necessary.

#     Args:
#         bbox1: (Tensor) bounding bboxes, sized [N, 4].
#         bbox2: (Tensor) bounding bboxes, sized [M, 4].
#     Returns:
#         (Tensor) IoU, sized [N, M].
#     """
#     N = bbox1.size(0)
#     M = bbox2.size(0)

#     # Compute left-top coordinate of the intersections
#     lt = torch.max(
#         bbox1[:, :2].unsqueeze(1).expand(N, M, 2), # [N, 2] -> [N, 1, 2] -> [N, M, 2]
#         bbox2[:, :2].unsqueeze(0).expand(N, M, 2)  # [M, 2] -> [1, M, 2] -> [N, M, 2]
#     )
#     # Conpute right-bottom coordinate of the intersections
#     rb = torch.min(
#         bbox1[:, 2:].unsqueeze(1).expand(N, M, 2), # [N, 2] -> [N, 1, 2] -> [N, M, 2]
#         bbox2[:, 2:].unsqueeze(0).expand(N, M, 2)  # [M, 2] -> [1, M, 2] -> [N, M, 2]
#     )
#     # Compute area of the intersections from the coordinates
#     wh = rb - lt   # width and height of the intersection, [N, M, 2]
#     wh[wh < 0] = 0 # clip at 0
#     inter = wh[:, :, 0] * wh[:, :, 1] # [N, M]

#     # Compute area of the bboxes
#     area1 = (bbox1[:, 2] - bbox1[:, 0]) * (bbox1[:, 3] - bbox1[:, 1]) # [N, ]
#     area2 = (bbox2[:, 2] - bbox2[:, 0]) * (bbox2[:, 3] - bbox2[:, 1]) # [M, ]
#     area1 = area1.unsqueeze(1).expand_as(inter) # [N, ] -> [N, 1] -> [N, M]
#     area2 = area2.unsqueeze(0).expand_as(inter) # [M, ] -> [1, M] -> [N, M]

#     # Compute IoU from the areas
#     union = area1 + area2 - inter # [N, M, 2]
#     iou = inter / union           # [N, M, 2]

#     return iou

# b1 = torch.tensor([[3.,3.,4.,4.], [1.,1.,2.,2.],])
# torch.sum(b1)
# mask = (b1 <= 2)
# b1[mask]
# b1m = b1[:,3] > 3
# b1m1 = b1m.unsqueeze(-1).expand_as(b1)
# b1m1 * b1
# b1[:, :2] = b1[:, :2] - b1[:, 2:4]*0.5
# b1.shape[0]
# b2 = torch.tensor([[1.5,1.5,2.5,2.5]])
# IoU = compute_iou(b1, b2)
# print(b1.size(), b2.size(), IoU.size())
# print(IoU)
# int(np.argmax(IoU))
# # compute_iou testing


# Training & Testing.
model = model.to(device)
for epoch in range(1, max_epoch):
    # Learning rate scheduling
    if epoch in [50, 150]:
        lr *= 0.1
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr

    if epoch < last_epoch:
        continue

    model.train()
    for x, y in train_dloader:
        # implement training pipeline here
    model.eval()
    with torch.no_grad():
        for x, y in test_dloader:
        # implement testing pipeline here
    
    ckpt = {'model':model.state_dict(),
            'optimizer':optimizer.state_dict(),
            'epoch':epoch}
    torch.save(ckpt, ckpt_path)