"""Predictor contract checks without loading model files or opening devices."""
import threading
import unittest
from unittest.mock import patch
import numpy as np
import torch
from core.uniform_model_registry import CandidatePredictor,CLASSES


class UniformPredictorBackendTests(unittest.TestCase):
    def test_unsupported_or_unavailable_backend_fails_before_weights(self):
        with patch('torch.load') as load:
            with self.assertRaises(ValueError):CandidatePredictor('/unused',backend='cuda')
            with patch('torch.backends.mps.is_available',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'no fallback'):CandidatePredictor('/unused',backend='mps')
            load.assert_not_called()

    def test_timed_path_keeps_rgb_float_letterbox_mapping_and_no_flip(self):
        received=[]
        class Model:
            def __call__(self,tensor):
                received.append(tensor)
                return torch.tensor([[.1,.2,.7]])
            def parameters(self):return iter((torch.zeros(1),))
        predictor=CandidatePredictor.__new__(CandidatePredictor)
        predictor.torch=torch;predictor.backend='cpu';predictor.lock=threading.Lock();predictor.model=Model()
        image=np.zeros((224,224,3),np.uint8)
        image[:,:112]=(10,20,30);image[:,112:]=(40,50,60)
        result=predictor.timed_probabilities(image)
        tensor=received[0]
        self.assertEqual(tuple(tensor.shape),(1,3,224,224));self.assertEqual(tensor.dtype,torch.float32)
        self.assertTrue(torch.allclose(tensor[0,:,100,20],torch.tensor([30,20,10])/255))
        self.assertTrue(torch.allclose(tensor[0,:,100,200],torch.tensor([60,50,40])/255))
        self.assertEqual(list(result['probabilities']),CLASSES)
        self.assertGreaterEqual(result['timings']['total_ms'],result['timings']['inference_ms'])
        self.assertEqual(result['backend']['model_parameter_device'],'cpu')


if __name__=='__main__':unittest.main()
