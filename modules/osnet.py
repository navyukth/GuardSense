import torch
import torch.nn.functional as F

from torchvision import transforms
from PIL import Image

import torchreid


class OSNet:

    def __init__(
        self,
        model_path="models/osnet_x1_0_msmt17.pth",
        device=None
    ):

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self.device = device
        print(f"Loading OSNet ({device})...")

        self.model = torchreid.models.build_model(
            name="osnet_x1_0",
            num_classes=1000,
            pretrained=False
        )

        state_dict = torch.load(
            model_path,
            map_location=device
        )

        self.model.load_state_dict(state_dict)
        self.model.to(device)
        self.model.eval()

        self.transform = transforms.Compose([
            transforms.Resize((256,128)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485,0.456,0.406],
                std=[0.229,0.224,0.225]
            )
        ])

        print("OSNet Ready.")

    def extract(
        self,
        frame,
        bbox
    ):

        x1,y1,x2,y2 = bbox

        crop = frame[y1:y2, x1:x2]

        if crop.size == 0:
            return None

        crop = crop[:,:,::-1]
        image = Image.fromarray(crop)
        tensor = self.transform(image)
        tensor = tensor.unsqueeze(0)
        tensor = tensor.to(self.device)

        with torch.no_grad():
            feature = self.model(tensor)
            feature = F.normalize(feature,p=2,dim=1)
        return feature.squeeze().cpu()

    @staticmethod
    def similarity(
        feature1,
        feature2
    ):

        return torch.dot(
            feature1,
            feature2
        ).item()