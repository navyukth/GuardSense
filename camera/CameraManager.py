import cv2

class CameraManager:
    def __init__(self):
        self.cameras = {}

    def add_cam(self,cam_id,src):
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            raise Exception(f"Unable to open Camera: {src}")
        
        self.cameras[cam_id] = cap

    def get_frame(self,cam_id):
        cap = self.cameras.get(cam_id)

        if cap is None:
            return None
        
        ret, frame = cap.read()

        if not ret:
            return None
        
        return frame

    def remove_came(self,cam_id):
        cap = self.cameras.pop(cam_id, None)

        if cap:
            cap.release()

    def stop(self):
        for cap in self.cameras.values():
            cap.release()
        
        self.cameras.clear()
