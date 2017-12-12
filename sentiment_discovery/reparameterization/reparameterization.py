import torch
from torch.nn.parameter import Parameter
import sys
class Reparameterization(object):
	"""
	Class interface for performing weight reparameterizations
	Arguments:
		name (str): name of weight parameter
		dim (int): dimension over which to compute the norm
		module (nn.Module): parent module to which param `name` is registered to
		retain_forward (bool, optional): if False deletes weight on call to 
			module.backward. Used to avoid memory leaks with DataParallel Default: True
	Attributes:
		reparameterization_names (list, str): contains names of all parameters 
			needed to compute reparameterization.
		backward_hook_key (int): torch.utils.hooks.RemovableHandle.id for hook used in module backward pass.
	"""

	def __init__(self, name, dim, module, retain_forward=True):
		self.name = name
		self.dim = dim
		self.evaluated = False
		self.retain_forward = retain_forward
		self.reparameterization_names = []
		self.backward_hook_key = None
		self.module = module

	def compute_weight(self, module):
		"""
		Computes reparameterized weight value to assign value to module attribute
		with name `name`.
		See WeightNorm class for example.
		Arguments:
			module (nn.Module): module with weight we'd like to reparameterize
		Returns:
			w (Tensor): Tensor object containing value of reparameterized weight
		"""
		raise NotImplementedError

	def reparameterize(self, name, weight, dim):
		"""
		Creates Parameters to be used for reparameterization and creates names that
		for attributes for the module these Parameters will correspond to.
		The parameters will be registered according to the names provided.
		See WeightNorm class for example.
		Arguments:
			module (nn.Module): module with weight we'd like to reparameterize
			name (str, optional): name of weight parameter
			dim (int, optional): dimension over which to compute parameterization
		Returns:
			names (list, str): names of Parameters to be used for reparameterization
			params (list, Parameter): Parameters to be used for reparameterization
		"""
		raise NotImplementedError

	@staticmethod
	def apply(module, name, dim, retain_forward=True, reparameterization=None, hook_child=True):
		"""
		Applies reparametrization to module's `name` parameter and modifies instance attributes as appropriate.
		`hook_child` adds reparameterization hook to direct parent of the parameters. If False, it's added to `module` instead.
		"""
		if reparameterization is None:
			reparameterization = Reparameterization
		name2use, module2use = Reparameterization.get_name_and_module(module, name)
		if name2use is None or isinstance(module2use, (torch.nn.Embedding, torch.nn.EmbeddingBag)):
			return

		fn = reparameterization(name2use, dim, module2use, retain_forward=retain_forward)

		weight = getattr(module2use, name2use)
		if weight.dim() <= 1:
			return

		# remove weight from parameter list
		del module2use._parameters[name2use]

		# add parameters of reparameterization of parameter to module
		names, params = fn.reparameterize(name2use, weight, dim)
		for n, p in zip(names, params):
			module2use.register_parameter(n, p)

		# add parameters to reparameterization so they can be removed later
		fn.reparameterization_names = names

		setattr(module2use, name2use, None)

		hook_module = module2use
		if not hook_child:
			hook_module = module
		# recompute weight before every forward()
		hook_module.register_forward_pre_hook(fn)

		# remove weight during backward
		handle = hook_module.register_backward_hook(fn.backward_hook)
		# get hook key so we can delete it later
		fn.backward_hook_key = handle.id

		return fn

	@staticmethod
	def get_name_and_module(module, name):
		"""
		recursively fetches (possible) child module and name of weight to be reparameterized
		"""
		name2use = None
		module2use = None
		names = name.split('.')
		if len(names) == 1 and names[0] != '':
			name2use = names[0]
			module2use = module
		elif len(names) > 1:
			module2use = module
			name2use = names[0]
			for i in range(len(names)-1):
				module2use = getattr(module2use, name2use)
				name2use = names[i+1]
		return name2use, module2use

	def get_params(self, module):
		"""gets params of reparameterization based on known attribute names"""
		return [getattr(module, n) for n in self.reparameterization_names]

	def remove(self, module):
		"""removes reparameterization and backward hook (does not remove forward hook)"""
		for p in self.get_params(self.module):
			p.requires_grad = False
		weight = self.compute_weight(self.module)
		delattr(self.module, self.name)
		for n in self.reparameterization_names:
			del self.module._parameters[n]
		self.module.register_parameter(self.name, Parameter(weight.data))
		del module._backward_hooks[self.backward_hook_key]

	def __call__(self, module, inputs):
		"""callable hook for forward pass"""
		sys.stdout.flush()
		_w = getattr(self.module, self.name)
		if not self.evaluated or _w is None:
			setattr(self.module, self.name, self.compute_weight(self.module))
			self.evaluated = True

	def backward_hook(self, module, grad_input, grad_output):
		"""callable hook for backward pass"""
		wn = getattr(self.module, self.name)
		if wn is not None and not self.retain_forward and self.evaluated:
			del wn.grad
			wn.grad = None
			setattr(self.module, self.name, None)
		self.evaluated = False
