import sys
import time

from numpy import mean, std, isnan, nan

from django.conf import settings

from base import DatazillaModelBase

class AlertsModel(DatazillaModelBase):
    """
    Public interface to all alert relevant data in the schema.
    """

    def __init__(self, project=None, metrics=()):

        super(AlertsModel, self).__init__(project)


    def insert_alert(self, data):
        pass

