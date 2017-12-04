# -*- coding: utf-8 -*-
# Copyright 2017 Dunbar Security Solutions, Inc.
#
# This file is part of Cyphon Engine.
#
# Cyphon Engine is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# Cyphon Engine is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Cyphon Engine. If not, see <http://www.gnu.org/licenses/>.
"""

"""

# standard library
import logging
import time

# third party
from celery import chain
from django.db import models
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _

# local
# from bottler.containers.models import Container
from cyphon.models import GetByNameManager
from cyphon.choices import TIME_UNIT_CHOICES
from cyphon.tasks import start_supplylink
from procurer.requisitions.models import Requisition, Parameter
import utils.dateutils.dateutils as dt
from .exceptions import SupplyChainError

_LOGGER = logging.getLogger(__name__)


class SupplyChainManager(GetByNameManager):
    """Manage |SupplyChain| objects.

    Adds methods to the default Django model manager.
    """

    def filter_by_user(self, user):
        """Get |SupplyChains| that can be executed by the given user.

        Ensures that every SupplyLink in the the SupplyChain can be
        executed by the given user.

        Returns
        -------
        |Queryset|
            A |Queryset| of |SupplyChains| can be executed by the given
            user.

        """
        queryset = self.get_queryset()
        excluded_supplylinks = SupplyLink.objects.exclude_by_user(user)
        return queryset.exclude(supplylink__in=excluded_supplylinks)


class SupplyChain(models.Model):
    """

    Attributes
    ----------
    name : str

    """

    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_('name')
    )

    objects = SupplyChainManager()

    class Meta(object):
        """Metadata options."""

        verbose_name = _('supply chain')
        verbose_name_plural = _('supply chains')

    def __init__(self, *args, **kwargs):
        """

        """
        super(SupplyChain, self).__init__(*args, **kwargs)
        self.is_valid = self.validate()
        self.errors = []

    @cached_property
    def _first_link(self):
        """Return the first SupplyLink in the SupplyChain."""
        return self.supplylinks.first()

    @cached_property
    def _last_link(self):
        """Return the last SupplyLink in the SupplyChain."""
        return self.supplylinks.last()

    @property
    def platform(self):
        """Return the Platform for the last SupplyLink in the SupplyChain."""
        return self._last_link.platform

    @property
    def input_fields(self):
        """
        Returns a dictionary in which keys are the names of input fields
        and the values are the field types.
        """
        return self._first_link.input_fields

    def validate(self):
        """

        """
        is_valid = True
        supply_links = self.supply_links.all()

        if supply_links:
            for supply_link in self.supply_links.all():
                supply_link.validate()
                if not supply_link.is_valid:
                    is_valid = False
                    self.errors += supply_link.errors
        else:
            is_valid = False
            self.errors = ['SupplyChain has no SupplyLinks.']

        self.is_valid = is_valid
        return self

    def validate_input(self, data):
        """
        Returns a dictionary in which keys are the names of input fields
        and the values are the field types.
        """
        return self._first_link.validate_input(data)

    def start(self, data, user):
        """

        """
        try:
            # use object ids instead of objects, which aren't JSON serializable
            # and can't be used in celery tasks
            id_list = list(self.supplylinks.all().values_list('pk', flat=True))
            links = [start_supplylink.s(data, id_list[0], user)]
            links += [
                start_supplylink.s(obj_id, user) for obj_id in id_list[1:]
            ]
            result = chain(*links)()
            return result.get()
        except SupplyChainError as error:
            _LOGGER.error('A SupplyChainError occurred: %s', error.msg)


class SupplyLinkManager(GetByNameManager):
    """Manage |SupplyLink| objects.

    Adds methods to the default Django model manager.
    """

    def filter_by_user(self, user):
        """Get |SupplyLinks| that can be executed by the given user.

        Returns
        -------
        |Queryset|
            A |Queryset| of |SupplyLinks| can be executed by the given
            user.

        """
        queryset = self.get_queryset()

        has_user = models.Q(requisition__emissary__passport__users=user)
        is_public = models.Q(requisition__emissary__passport__public=True)

        return queryset.filter(has_user | is_public).distinct()

    def exclude_by_user(self, user):
        """Get |SupplyLinks| that cannot be executed by the given user.

        Returns
        -------
        |Queryset|
            A |Queryset| of |SupplyLinks| can be executed by the given
            user.

        """
        queryset = self.get_queryset()
        available_supplylinks = self.filter_by_user()
        ids = available_supplylinks.values_list('id', flat=True)
        return queryset.exclude(id__in=ids)


class SupplyLink(models.Model):
    """

    Attributes
    ----------
    supply_chain : SupplyChain
        The |SupplyChain| associated with the SupplyLink.

    requisition : Requisition
        The |Requisition| associated with the SupplyLink.

    position : int
        An |int| representing the order of the SupplyLink in a
        |SupplyChain|. SupplyLinks are evaluated in ascending order (the
        lowest rank first)

    wait_time : int

    time_unit : str

    """

    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_('name')
    )
    supply_chain = models.ForeignKey(
        SupplyChain,
        related_name='supplylinks',
        related_query_name='supplylink',
        verbose_name=_('supply chain')
    )
    requisition = models.ForeignKey(Requisition, verbose_name=_('requisition'))
    position = models.IntegerField(
        default=0,
        verbose_name=_('position'),
        help_text=_('An integer representing the order of this step in the '
                    'Supply Chain. Steps are performed in ascending order, '
                    'with the lowest number performed first.')
    )
    wait_time = models.IntegerField(
        default=0,
        verbose_name=_('wait interval')
    )
    time_unit = models.CharField(
        max_length=3,
        default='m',
        choices=TIME_UNIT_CHOICES,
        verbose_name=_('time unit')
    )

    objects = SupplyLinkManager()

    class Meta(object):
        """Metadata options."""

        ordering = ['supply_chain', 'position']
        unique_together = ['supply_chain', 'position']
        verbose_name = _('supply link')
        verbose_name_plural = _('supply links')

    def __init__(self, *args, **kwargs):
        """

        """
        super(SupplyLink, self).__init__(*args, **kwargs)
        self.is_valid = self.validate()
        self.errors = []

    def __str__(self):
        """

        """
        return '<SupplyLink: %s>' % self.name

    @cached_property
    def input_fields(self):
        """
        Returns a dictionary in which keys are the names of input fields
        and the values are the field types.
        """
        input_fields = {}
        for field_coupling in self.field_couplings.all():
            input_fields.update(field_coupling.input_field)
        return input_fields

    @cached_property
    def coupling(self):
        """
        Returns a dictionary in which the keys are field names that
        correspond to the keys of input data and the values are the
        names of the parameters for which those fields will supply
        values.
        """
        coupling = {}
        for field_coupling in self.field_couplings.all():
            coupling.update(field_coupling.mapping)
        return coupling

    @property
    def countdown_seconds(self):
        """
        Returns the number of seconds bedore.
        """
        return dt.convert_time_to_seconds(self.wait_time, self.time_unit)

    @property
    def platform(self):
        """Return the Platform associated with the SupplyLink's Requisition."""
        return self.requisition.platform

    @cached_property
    def _coupling_parameters(self):
        """

        """
        return [coupling.parameter for coupling in self.field_couplings.all()]

    @cached_property
    def _required_parameters(self):
        """

        """
        return self.requisition.required_parameters

    def validate(self):
        """

        """
        is_valid = True
        self.errors = []

        for req_param in self._required_parameters:
            if req_param not in self._coupling_parameters:
                is_valid = False
                self.errors.append('A FieldCoupling is missing for the '
                                   'required parameter %s.' % req_param)

        self.is_valid = is_valid
        return self

    def _get_params(self, data):
        """

        """
        params = {}
        for (field_name, param_name) in self.coupling.items():
            params[param_name] = data.get(field_name)
        return params

    def _create_transport(self, user):
        """

        """
        return self.requisition.create_request_handler(user=user)

    def validate_input(self, data):
        """

        """
        is_valid = True
        invalid_couplings = []

        for coupling in self.field_couplings.all():
            if not coupling.validate_input(data):
                is_valid = False
                invalid_couplings.append(str(coupling))

        if not is_valid:
            error_msg = ('The following couplings were invalid: %s'
                         % invalid_couplings)
            raise SupplyChainError(error_msg)

        return is_valid

    def process(self, data, user):
        """

        Parameters
        ----------
        user : |AppUser|
            The user making the API request.

        data : |dict|
            A dictionary of data used to construct the API request.

        Returns
        -------
        |Convoy|

        Raises
        ------
        SupplyChainError

        """
        self.validate_input(data)

        params = self._get_params(data)
        transport = self._create_transport(user)
        time.sleep(self.countdown_seconds)
        transport.run(params)

        if transport.cargo:
            return transport.cargo.data
        else:
            # TODO(LH): add message to exception
            raise SupplyChainError()


class FieldCoupling(models.Model):
    """

    Attributes
    ----------
    supply_link : SupplyLink
        The |SupplyLink| associated with the Coupling.

    field_name : str
        The dictionary key storing the value for the `parameter`.

    parameter : Parameter
        The |Parameter| that will be supplied a value.

    """

    supply_link = models.ForeignKey(
        SupplyLink,
        related_name='field_couplings',
        related_query_name='field_coupling',
        verbose_name=_('supply link')
    )
    parameter = models.ForeignKey(
        Parameter,
        related_name='field_couplings',
        related_query_name='field_coupling',
        verbose_name=_('parameter')
    )
    field_name = models.CharField(
        max_length=64,
        verbose_name=_('field name')
    )

    class Meta(object):
        """Metadata options."""

        ordering = ['supply_link', 'parameter']
        unique_together = ['supply_link', 'parameter']
        verbose_name = _('supply link')
        verbose_name_plural = _('supply links')

    def __str__(self):
        """String representation of a FieldCoupling."""
        return '%s:%s' % (self.supply_link, self.parameter)

    @cached_property
    def _param_name(self):
        """

        """
        return self.parameter.param_name

    @cached_property
    def _param_type(self):
        """

        """
        return self.parameter.param_type

    @property
    def mapping(self):
        """

        """
        return {self.field_name: self._param_name}

    @cached_property
    def input_field(self):
        """

        """
        return {self.field_name: self._param_type}

    def validate_input(self, data):
        """

        """
        value = data.get(self.field_name)
        return self.parameter.validate(value)