from modules.osnet import OSNet

class EmbeddingFilter:
    def __init__(self,osnet,max_embeddings=5,similarity_threshold=0.98):
        self.max_embeddings = max_embeddings
        self.similarity_threshold = similarity_threshold
        self.track_embeddings = {}
        self.osnet = osnet

    def add(self,track_id,embedding):
        if embedding is None:
            return False

        if track_id not in self.track_embeddings:
            self.track_embeddings[track_id] = [embedding]
            return True
        
        bank = self.track_embeddings[track_id]
        similarities = [
            self.osnet.similarity(
                embedding,
                old_embedding
            )
            for old_embedding in bank
        ]

        if max(similarities) >= self.similarity_threshold:            
            print("--------------------------------")
            print(f"Track: {track_id}")
            print(f"Max Similarity: {max(similarities):.4f}")
            print(f"All: {[round(s,4) for s in similarities]}")
            return False

        if len(bank) < self.max_embeddings:
            bank.append(embedding)
            
            return True
        
        return False

    def get(self,track_id):
        return self.track_embeddings.get(track_id, [])

    def clear(self,track_id):
        if track_id in self.track_embeddings:

            del self.track_embeddings[track_id]