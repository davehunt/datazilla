import sys
import time
import json

from collections import defaultdict

from django.conf import settings

from dzmetrics.ttest import welchs_ttest_internal

from base import DatazillaModelBase
from .alert_metrics.stats import (
        Z_moment, stats2z_moment, Stats, z_moment2stats, single_ttest)

class AlertsModel(DatazillaModelBase):
    """
    Public interface to all alert relevant data in the schema.
    """

    CONTENT_TYPES = ["perftest", "objectstore"]

    SEVERITY = 0.6 #There are many false positives
    MIN_CONFIDENCE = 0.99
    WINDOW_SIZE = 10

    def __init__(self, project=None, metrics=()):

        super(AlertsModel, self).__init__(project)

        self.datumLimit = 50

    def claim_object_test_run_ids(self, limit):

        """
        Claim & return up to ``limit`` objectstore test_run_id's with a
        processed_flag of summary_ready.

        Returns a list of test_run_ids.

        May return more than ``limit`` rows if there are existing orphaned rows
        that were claimed by an earlier connection with the same connection ID
        but never completed.
        """
        proc_mark = 'objectstore.alerts.updates.mark_summary_loading'
        proc_get  = 'objectstore.alerts.selects.get_claimed_summary'

        # NOTE: Disabling warnings here.  A warning is generated in the
        # production environment that is specific to the master/slave
        # configuration. It's caused by the LIMIT in the SQL query, this
        # this work fine but the mysql warning kills the executing process.
        filterwarnings('ignore', category=MySQLdb.Warning)

        # Note: this claims rows for processing. Failure to call process_summary
        # on this data will result in some json blobs being stuck in limbo
        # until another worker comes along with the same connection ID.
        self.sources["objectstore"].dhub.execute(
            proc=proc_mark,
            placeholders=[ limit ],
            debug_show=self.DEBUG,
            )

        # Return all JSON blobs claimed by this connection ID (could possibly
        # include orphaned rows from a previous run).
        test_run_ids = self.sources["objectstore"].dhub.execute(
            proc=proc_get,
            debug_show=self.DEBUG,
            return_type='tuple'
            )

        resetwarnings()

        return test_run_ids

    def mark_summary_ready(self, test_run_ids):
        """ Call to database to mark objects ready for summary processing """

        placeholders = []
        map(lambda x:placeholders.append(x), test_run_ids)

        self.sources["objectstore"].dhub.execute(
            proc="objectstore.alerts.updates.mark_summary_ready",
            placeholders=placeholders,
            executemany=True,
            debug_show=self.DEBUG
            )

    def mark_summary_complete(self, test_run_ids):
        """ Call to database to mark objects summary process completed """

        placeholders = []
        map(lambda x:placeholders.append(x), test_run_ids)

        self.sources["objectstore"].dhub.execute(
            proc="objectstore.alerts.updates.mark_summary_complete",
            placeholders=placeholders,
            executemany=True,
            debug_show=self.DEBUG
            )

    def mark_summary_error(self, test_run_id, error):
        """ Call to database to mark objects errored while generating a summary"""
        self.sources["objectstore"].dhub.execute(
            proc="objectstore.alerts.updates.mark_summary_error",
            placeholders=[error, test_run_id],
            executemany=True,
            debug_show=self.DEBUG
            )

    def process_summary(self, test_run_ids):

        #REMOVE
        #test_run_ids = [183666, 183646, 183630, 183590, 183568, 183550, 183515, 183439, 183366, 183352, 183256, 100187, 99900, 99887, 94626 ]
        #test_run_ids = [183666, 183646]
        test_run_ids = [183646]
        test_run_id_set = set(test_run_ids)

        #Retrieve reference data associated with these test_run_ids
        proc = "perftest.alerts.selects.get_all_dimensions_ref_data"

        ref_data = self.sources["perftest"].dhub.execute(
            proc="perftest.alerts.selects.get_all_dimensions_ref_data",
            replace=[test_run_ids],
            debug_show=self.DEBUG
            )

        summary_collection = SummaryCollection()

        for ref_datum in ref_data:

            placeholders = [
                ref_datum['product_id'],
                ref_datum['operating_system_id'],
                ref_datum['test_id'],
                ref_datum['page_id'],
                ref_datum['branch'],
                ref_datum['branch_version'],
                ref_datum['processor'],
                ref_datum['build_type'],
                self.datumLimit
                ]

            #For each unique set of reference data retrieve the last
            #self.datumLimit of datapoints. Order by
            #coalesce(push_date, date_received)
            datum_set = self.sources["perftest"].dhub.execute(
                proc="perftest.alerts.selects.get_all_dimensions_datum_set",
                placeholders=placeholders,
                debug_show=self.DEBUG
                )

            self.process_datum_set(
                datum_set, summary_collection, test_run_id_set)

        #Compute percentages and number of total objects in
        #summary structures
        summary_collection.compute()

        #REMOVE THIS LINE, for testing only
        summary_collection.print_summaries()

    def process_datum_set(
        self, datum_set, summary_collection, test_run_id_set):
        """
                datum_set = [
                    {
                        product_id:"",
                        operating_system_id:"",
                        test_id:"",
                        test_name:"",
                        product:"",
                        branch:"",
                        branch_version:"",
                        revision:"",
                        operating_system_name:"",
                        operating_system_version:"",
                        processor:"",
                        page_id:"",
                        page_url:"",
                        test_run_id:"",
                        coalesce(push_date, date_received) AS push_date:"",
                        n_replicates:"",
                        mean:"",
                        std:""
                        },
                    ...
                    ]
        """
        #use zmoment_total for total rolling stats accumulation
        zmoment_total = Z_moment()
        total_values = len(datum_set)

        for count, v in enumerate(datum_set):
            print v['page_url']
            #The inter-test variance is significant and can
            #not be explained. We simply consider test series
            #a single sample.
            s = Stats(count=1, mean=v['mean'], biased=True)

            if v['test_run_id'] in test_run_id_set:
                t = z_moment2stats(zmoment_total, unbiased=False)

                #Assume uniform distribution if variance is too small
                confidence, diff = single_ttest(
                    s.mean, t, min_variance=1.0/12.0
                    )

                if AlertsModel.MIN_CONFIDENCE < confidence and diff > 0:

                    #Just setting placeholders here until the correct
                    #computations are determined
                    v['test_evaluation'] = 0
                    v['h0_rejected'] = 1
                    v['pass'] = 0
                    v['fail'] = 1

                else:
                    #Just setting placeholders here until the correct
                    #computations are determined
                    v['test_evaluation'] = 1
                    v['h0_rejected'] = 0
                    v['pass'] = 1
                    v['fail'] = 0

                summary_collection.add(v)

                summary_collection.print_summaries()

            zmoment = stats2z_moment(s, self.DEBUG)

            #Add a zmoment attribute to each value dict
            datum_set[count - 1]['zmoment'] = zmoment
            #Calculate cumulative zmoment
            zmoment_total = zmoment_total + zmoment

            if count >= AlertsModel.WINDOW_SIZE:
                #Limit window in zmoment_total according to WINDOW_SIZE
                ds_index = count - AlertsModel.WINDOW_SIZE
                zmoment_total = zmoment_total - datum_set[ds_index]['zmoment']

class SummaryCollection():

    def __init__(self):

        self.summaries = {}

    def add(self, datum):

        product_id = datum['product_id']
        revision = datum['revision']

        if product_id not in self.summaries:
            self.summaries[ product_id ] = {}

        if revision not in self.summaries[ product_id ]:
            self.summaries[ product_id ][ revision ] = RevisionSummary()

        self.summaries[ product_id ][ revision ].add(datum)

    def compute(self):
        for product_id in self.summaries:
            for revision in self.summaries[product_id]:
                self.summaries[product_id][revision].compute()

    def print_summaries(self):
        for product_id in self.summaries:
            for revision in self.summaries[product_id]:
                json_object = self.summaries[product_id][revision].get_json()
                print json_object

class RevisionSummary():
    """
    Encapsulats building operations for a revision summary.
    """
    def __init__(self):

        self.data = {
            'revision':"",
            'percent_pass':0.00,
            'total_objects':0,
            'test_run_ids':{},
            'total_pass':0,
            'total_fail':0,
            'last_update':0,
            'push_date':0,
            'product':{},
            'platforms':{},
            'tests':{},
            'tests_vs_platforms':{}
            }

    @classmethod
    def get_platform_name(self, datum):

        return "{0} {1} {2}".format(
            datum['operating_system_name'],
            datum['operating_system_version'],
            datum['processor'])

    def get_json(self):
        return json.dumps(self.data)

    def add(self, datum):

        #The object count is equal to the number of
        #unique test_run_ids
        self.data['test_run_ids'][ datum['test_run_id'] ] = True

        platform = RevisionSummary.get_platform_name(datum)
        test_name = datum['test_name']
        page_url = datum['page_url']

        self.init_summary(datum, test_name, platform, page_url)

        self.data['platforms'][platform]['test_run_ids'][ datum['test_run_id'] ] = True
        self.data['tests'][test_name]['test_run_ids'][ datum['test_run_id'] ] = True

        self.data['platforms'][platform]['total_pass'] += datum['pass']
        self.data['platforms'][platform]['total_fail'] += datum['fail']

        self.data['tests'][test_name]['total_pass'] += datum['pass']
        self.data['tests'][test_name]['total_fail'] += datum['fail']

        self.data['tests_vs_platforms'][test_name][platform]['total_pass'] += datum['pass']
        self.data['tests_vs_platforms'][test_name][platform]['total_fail'] += datum['fail']

    def compute(self):

        #Total number of objects is equal to the number of test run ids
        self._set_total_objects(self.data)
        self._set_percentage(self.data)

        for key_one in ['platforms', 'tests']:
            for key_two in self.data[key_one]:

                self._set_percentage(self.data[key_one][key_two])
                self._set_total_objects(self.data[key_one][key_two])

        for test_name in self.data['tests_vs_platforms']:
            for platform in self.data['tests_vs_platforms'][test_name]:
                self._set_percentage(self.data['tests_vs_platforms'][test_name][platform])

    def merge(self):
        pass

    def _set_percentage(self, data):

        total = data['total_pass'] + data['total_fail']

        percentage = 0.00
        if total > 0:
            percentage = round( (float(data['total_pass'])/float(total))*100.00 )

        data['percent_pass'] = percentage

    def _set_total_objects(self, data):

        #Total number of objects is equal to the number of test run ids
        data['total_objects'] = len( data['test_run_ids'].keys() )

    def init_summary(self, datum, test_name, platform, page_url):

        if not self.data['revision']:
            self.data['revision'] = datum['revision']

        if not self.data['push_date']:
            self.data['push_date'] = datum['push_date']

        if platform not in self.data['platforms']:

            self.data['platforms'][platform] = {
                'os':datum['operating_system_name'],
                'os_version':datum['operating_system_version'],
                'processor':datum['processor'],
                'percent_pass':0.00,
                'total_objects':0,
                'test_run_ids':{},
                'total_pass':0,
                'total_fail':0
                }

        if test_name not in self.data['tests']:

            self.data['tests'][test_name] = {
                'total_pages':0,
                'percent_pass':0.00,
                'total_objects':0,
                'test_run_ids':{},
                'total_pass':0,
                'total_fail':0,
                'failure_probability':0.0000
                }

            self.data['tests_vs_platforms'][test_name] = {}

        if platform not in self.data['tests_vs_platforms'][test_name]:

            self.data['tests_vs_platforms'][test_name][platform] = {
                'machine':datum['machine_name'],
                'total_pages':0,
                'percent_pass':0.00,
                'total_pass':0,
                'total_fail':0,
                'failure_probability':0.0000,
                'pages': {}
                }

        if page_url not in self.data['tests_vs_platforms'][test_name][platform]['pages']:
            self.data['tests_vs_platforms'][test_name][platform]['pages'][page_url] = {
                'mean':datum['mean'],
                'std':datum['std'],
                'n_replicates':datum['n_replicates'],
                'p':0.000,
                'h0_rejected':datum['h0_rejected'],
                'test_evaluation':datum['test_evaluation']
                }

class RevisionSummaryError(ValueError):
    pass



