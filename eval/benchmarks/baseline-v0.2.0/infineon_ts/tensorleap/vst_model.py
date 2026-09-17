"""Architecture of the full VST model (encoder / decoder / discriminator / regressor).

Copied verbatim from notebooks/VST__Model_onnx_with_TrainingDataloader.ipynb so the pickled
model/enhanced_trial_19_full_model_complete.pth can be unpickled by tensorleap/generate_sidecars.py.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Function

class EnhancedCNNEncoder(nn.Module):
    def __init__(self, seq_len, latent_dim, num_filters=[16, 32, 64], 
                 kernel_sizes=[3, 3, 3], activation='GELU', dropout_rate=0.1,
                 use_batch_norm=True):
        super().__init__()
        
        self.seq_len = seq_len
        self.num_layers = len(num_filters)
        self.num_filters = num_filters
        
        if activation == 'GELU':
            self.activation = nn.GELU()
        elif activation == 'ReLU':
            self.activation = nn.ReLU()
        elif activation == 'SiLU':
            self.activation = nn.SiLU()
        else:
            self.activation = nn.GELU()
            
        layers = []
        in_channels = 2
        
        for i, (out_channels, kernel_size) in enumerate(zip(num_filters, kernel_sizes)):
            layers.append(nn.Conv1d(in_channels, out_channels, 
                                  kernel_size=kernel_size, padding=kernel_size//2))
            if use_batch_norm:
                layers.append(nn.BatchNorm1d(out_channels))
            layers.append(self.activation)
            if dropout_rate > 0:
                layers.append(nn.Dropout1d(dropout_rate))
            if i < self.num_layers - 1:
                layers.append(nn.MaxPool1d(kernel_size=2, stride=2))
            in_channels = out_channels
        
        self.cnn_layers = nn.Sequential(*layers)
        
        self.final_seq_len = seq_len
        for i in range(self.num_layers - 1):
            self.final_seq_len = self.final_seq_len // 2
            
        self.final_channels = num_filters[-1]
        self.final_feature_size = self.final_channels * self.final_seq_len
        
        self.fc = nn.Linear(self.final_feature_size, latent_dim)
        self.fc_dropout = nn.Dropout(dropout_rate) if dropout_rate > 0 else nn.Identity()
        
    def forward(self, current, deformation):
        current = current.unsqueeze(1)
        deformation = deformation.unsqueeze(1)
        x = torch.cat([current, deformation], dim=1)
        x = self.cnn_layers(x)
        x = x.view(x.size(0), -1)
        x = self.fc_dropout(x)
        z = self.fc(x)
        return z

class EnhancedCNNDecoder(nn.Module):
    def __init__(self, latent_dim, output_dim, encoder_final_seq_len, encoder_final_channels,
                 num_filters=[64, 32, 16], kernel_sizes=[4, 4, 3], activation='GELU', 
                 dropout_rate=0.1, use_batch_norm=True):
        super().__init__()
        
        self.output_dim = output_dim
        self.encoder_final_seq_len = encoder_final_seq_len
        self.encoder_final_channels = encoder_final_channels
        self.num_layers = len(num_filters)
        
        if activation == 'GELU':
            self.activation = nn.GELU()
        elif activation == 'ReLU':
            self.activation = nn.ReLU()
        elif activation == 'SiLU':
            self.activation = nn.SiLU()
        else:
            self.activation = nn.GELU()
        
        self.fc = nn.Linear(latent_dim, encoder_final_channels * encoder_final_seq_len)
        self.fc_dropout = nn.Dropout(dropout_rate) if dropout_rate > 0 else nn.Identity()
        
        layers = []
        in_channels = encoder_final_channels
        
        for i, (out_channels, kernel_size) in enumerate(zip(num_filters, kernel_sizes[:-1])):
            layers.append(nn.ConvTranspose1d(in_channels, out_channels, 
                                           kernel_size=kernel_size, stride=2, padding=1))
            if use_batch_norm:
                layers.append(nn.BatchNorm1d(out_channels))
            layers.append(self.activation)
            if dropout_rate > 0:
                layers.append(nn.Dropout1d(dropout_rate))
            in_channels = out_channels
        
        if len(kernel_sizes) > 0:
            layers.append(nn.Conv1d(in_channels, 1, kernel_size=kernel_sizes[-1], 
                                   padding=kernel_sizes[-1]//2))
        else:
            layers.append(nn.Conv1d(in_channels, 1, kernel_size=3, padding=1))
        
        self.conv_transpose_layers = nn.Sequential(*layers)
        self.expected_size = encoder_final_seq_len * (2 ** (len(num_filters)))
        self.adaptive_pool = nn.AdaptiveAvgPool1d(output_dim)
        
    def forward(self, z):
        x = self.fc_dropout(z)
        x = self.fc(x)
        x = x.view(x.size(0), self.encoder_final_channels, self.encoder_final_seq_len)
        x = self.conv_transpose_layers(x)
        x = x.squeeze(1)
        if x.size(1) != self.output_dim:
            x = x.unsqueeze(1)
            x = self.adaptive_pool(x)
            x = x.squeeze(1)
        return x

class GradientReversalFunction(Function):
    @staticmethod
    def forward(ctx, x, lambda_grl):
        ctx.lambda_grl = lambda_grl
        return x.view_as(x)
    
    @staticmethod
    def backward(ctx, grad_output):
        return -ctx.lambda_grl * grad_output, None

class GradientReversalLayer(nn.Module):
    def __init__(self, lambda_grl=1.0):
        super().__init__()
        self.lambda_grl = lambda_grl
    
    def forward(self, x):
        return GradientReversalFunction.apply(x, self.lambda_grl)

class DCBDiscriminator(nn.Module):
    def __init__(self, latent_dim, num_dcb_classes, hidden_dim=64, lambda_grl=1.0):
        super().__init__()
        self.grl = GradientReversalLayer(lambda_grl=lambda_grl)
        self.discriminator = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_dim, num_dcb_classes)
        )
    
    def forward(self, z):
        z_reversed = self.grl(z)
        logits = self.discriminator(z_reversed)
        return logits

class EnhancedPINN_CNN_Regressor_WithDiscriminator(nn.Module):
    def __init__(self, seq_len, latent_dim, output_dim, num_dcb_classes, hidden_dim=12,
                 encoder_config=None, decoder_config=None, regressor_dropout=0.2, lambda_grl=1.0):
        super().__init__()
        
        default_encoder_config = {
            'num_filters': [32, 64, 128],
            'kernel_sizes': [5, 3, 3],
            'activation': 'GELU',
            'dropout_rate': 0.15,
            'use_batch_norm': True
        }
        
        default_decoder_config = {
            'num_filters': [128, 64, 32],
            'kernel_sizes': [4, 4, 3],
            'activation': 'GELU',
            'dropout_rate': 0.15,
            'use_batch_norm': True
        }
        
        if encoder_config:
            default_encoder_config.update(encoder_config)
        if decoder_config:
            default_decoder_config.update(decoder_config)
            
        self.encoder = EnhancedCNNEncoder(seq_len, latent_dim, **default_encoder_config)
        self.decoder = EnhancedCNNDecoder(
            latent_dim=latent_dim, 
            output_dim=output_dim,
            encoder_final_seq_len=self.encoder.final_seq_len,
            encoder_final_channels=self.encoder.final_channels,
            **default_decoder_config
        )
        
        self.latent_dim = latent_dim
        self.z_feature_dim = latent_dim - 3
        
        self.regressor_pre = nn.Sequential(
            nn.Linear(self.z_feature_dim, hidden_dim * 2),
            nn.GELU(),
            nn.BatchNorm1d(hidden_dim * 2),
            nn.Dropout(regressor_dropout),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.GELU(),
            nn.BatchNorm1d(hidden_dim),
            nn.Dropout(regressor_dropout),
            nn.Linear(hidden_dim, self.z_feature_dim)
        )
        
        self.residual_weight = nn.Parameter(torch.tensor(0.5))
        
        self.regressor_post = nn.Sequential(
            nn.Linear(latent_dim + 1, hidden_dim * 2),
            nn.GELU(),
            nn.BatchNorm1d(hidden_dim * 2),
            nn.Dropout(regressor_dropout),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.GELU(),
            nn.BatchNorm1d(hidden_dim),
            nn.Dropout(regressor_dropout),
            nn.Linear(hidden_dim, 1)
        )
        
        self.discriminator = DCBDiscriminator(latent_dim, num_dcb_classes, hidden_dim=64, lambda_grl=lambda_grl)
        
    def forward(self, current, deformation, current_lengths=None, deformation_lengths=None, return_dcb_logits=False):
        z = self.encoder(current, deformation)
        z_feature = z[:, :-3]
        
        m = F.softplus(z[:, -3]) + 1e-6
        c = F.softplus(z[:, -2]) + 1e-6
        k = F.softplus(z[:, -1]) + 1e-6
        omega_n = torch.sqrt(k / m)
        
        recon = self.decoder(z)
        
        h = self.regressor_pre(z_feature)
        h = self.residual_weight * h + (1 - self.residual_weight) * z_feature
        
        physics_cat = torch.stack([m, c, k, omega_n], dim=1)
        h_cat = torch.cat([h, physics_cat], dim=-1)
        quality = self.regressor_post(h_cat).squeeze(-1)
        
        if return_dcb_logits:
            dcb_logits = self.discriminator(z)
            return recon, m, c, k, quality, dcb_logits
        
        return recon, m, c, k, quality

