# -*- coding: utf-8 -*- vim:fileencoding=utf-8:
# vim: tabstop=4:shiftwidth=4:softtabstop=4:expandtab

# Copyright (C) 2010-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import urllib2
import socket
import json
from django import forms
from django.views.decorators.csrf import csrf_exempt
from django.core import urlresolvers
from django.core import serializers
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.sites.models import Site
from django.contrib.auth.models import User
from django.http import HttpResponseRedirect, HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404, render_to_response
from django.core.context_processors import request
from django.template.context import RequestContext
from django.template.loader import get_template, render_to_string
from django.utils.translation import ugettext as _
from django.core.urlresolvers import reverse
from django.contrib import messages
from accounts.models import *
from ipaddr import *

from django.contrib.auth import authenticate, login

from django.forms.models import model_to_dict

from flowspec.forms import *
from flowspec.models import *
from peers.models import *

from registration.models import RegistrationProfile

from copy import deepcopy
from utils.decorators import shib_required

from django.views.decorators.cache import never_cache
from django.conf import settings
from django.template.defaultfilters import slugify
from flowspec.helpers import send_new_mail, get_peer_techc_mails
import datetime
import os

LOG_FILENAME = os.path.join(settings.LOG_FILE_LOCATION, 'celery_jobs.log')
#FORMAT = '%(asctime)s %(levelname)s: %(message)s'
#logging.basicConfig(format=FORMAT)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(clientip)s %(user)s: %(message)s')

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(LOG_FILENAME)
handler.setFormatter(formatter)
logger.addHandler(handler)

@login_required
def user_routes(request):
    user_routes = Route.objects.filter(applier=request.user)
    return render_to_response('user_routes.html', {'routes': user_routes},
                              context_instance=RequestContext(request))

def welcome(request):
    return render_to_response('welcome.html', context_instance=RequestContext(request))

@login_required
@never_cache
def dashboard(request):
    group_routes = []
    try:
        peer = request.user.get_profile().peer
    except UserProfile.DoesNotExist:
        error = "User <strong>%s</strong> does not belong to any peer or organization. It is not possible to create new firewall rules.<br>Please contact Helpdesk to resolve this issue" % request.user.username
        return render_to_response('error.html', {'error': error}, context_instance=RequestContext(request))
    if peer:
       peer_members = UserProfile.objects.filter(peer=peer)
       users = [prof.user for prof in peer_members]
       group_routes = Route.objects.filter(applier__in=users).order_by('-expires')[:10]
       if request.user.is_superuser:
           group_routes = Route.objects.all().order_by('-expires')[:10]
       return render_to_response('dashboard.html', {'routes': group_routes},
                              context_instance=RequestContext(request))

@login_required
@never_cache
def group_routes(request):
    try:
        peer = request.user.get_profile().peer
    except UserProfile.DoesNotExist:
        error = "User <strong>%s</strong> does not belong to any peer or organization. It is not possible to create new firewall rules.<br>Please contact Helpdesk to resolve this issue" % request.user.username
        return render_to_response('error.html', {'error': error}, context_instance=RequestContext(request))
    return render_to_response('user_routes.html', context_instance=RequestContext(request))

@login_required
@never_cache
def group_routes_ajax(request):
    group_routes = []
    try:
        peer = request.user.get_profile().peer
    except UserProfile.DoesNotExist:
        error = "User <strong>%s</strong> does not belong to any peer or organization. It is not possible to create new firewall rules.<br>Please contact Helpdesk to resolve this issue" % request.user.username
        return render_to_response('error.html', {'error': error}, context_instance=RequestContext(request))
    if peer:
       peer_members = UserProfile.objects.filter(peer=peer)
       users = [prof.user for prof in peer_members]
       group_routes = Route.objects.filter(applier__in=users)
       if request.user.is_superuser:
           group_routes = Route.objects.all()
    jresp = {}
    routes = build_routes_json(group_routes)
    jresp['aaData'] = routes
    return HttpResponse(json.dumps(jresp), mimetype='application/json')


@login_required
@never_cache
def overview_routes_ajax(request):
    group_routes = []
    try:
        peer = request.user.get_profile().peer
    except UserProfile.DoesNotExist:
        error = "User <strong>%s</strong> does not belong to any peer or organization. It is not possible to create new firewall rules.<br>Please contact Helpdesk to resolve this issue" % request.user.username
        return render_to_response('error.html', {'error': error}, context_instance=RequestContext(request))
    if peer:
       peer_members = UserProfile.objects.filter(peer=peer)
       users = [prof.user for prof in peer_members]
       group_routes = Route.objects.filter(applier__in=users)
       if request.user.is_superuser or request.user.has_perm('accounts.overview'):
           group_routes = Route.objects.all()
    jresp = {}
    routes = build_routes_json(group_routes)
    jresp['aaData'] = routes
    return HttpResponse(json.dumps(jresp), mimetype='application/json')


def build_routes_json(groutes):
    routes = []
    for r in groutes:
        rd = {}
        rd['id'] = r.pk
        rd['name'] = r.name
        if not r.comments:
            rd['comments'] = 'Not Any'
        else:
            rd['comments'] = r.comments
        rd['match'] = r.get_match()
        rd['then'] = r.get_then()
        rd['status'] = r.status
        rd['applier'] = r.applier.username
        try:
            rd['peer'] = r.applier.get_profile().peer.peer_name
        except UserProfile.DoesNotExist:
            rd['peer'] = ''
        rd['expires'] = "%s" %r.expires
        rd['response'] = "%s" %r.response
        routes.append(rd)
    return routes


@login_required
@never_cache
def add_route(request):
    applier = request.user.pk
    applier_peer_networks = request.user.get_profile().peer.networks.all()
    if request.user.is_superuser:
        applier_peer_networks = PeerRange.objects.all()
    if not applier_peer_networks:
        messages.add_message(
            request,
            messages.WARNING,
            ('Insufficient rights on administrative networks. Cannot add rule. Contact your administrator')
        )
        return HttpResponseRedirect(reverse("group-routes"))
    if request.method == "GET":
        form = RouteForm(initial={'applier': applier})
        if not request.user.is_superuser:
            form.fields['then'] = forms.ModelMultipleChoiceField(queryset=ThenAction.objects.filter(action__in=settings.UI_USER_THEN_ACTIONS).order_by('action'), required=True)
            form.fields['protocol'] = forms.ModelMultipleChoiceField(queryset=MatchProtocol.objects.filter(protocol__in=settings.UI_USER_PROTOCOLS).order_by('protocol'), required=False)
        return render_to_response('apply.html', {'form': form, 'applier': applier},
                                  context_instance=RequestContext(request))

    else:
        request_data = request.POST.copy()
        if request.user.is_superuser:
            request_data['issuperuser'] = request.user.username
        else:
            request_data['applier'] = applier
            try:
                del requset_data['issuperuser']
            except:
                pass
        form = RouteForm(request_data)
        if form.is_valid():
            route = form.save(commit=False)
            if not request.user.is_superuser:
                route.applier = request.user
            route.status = "PENDING"
            route.response = "Applying"
            route.source = IPNetwork('%s/%s' % (IPNetwork(route.source).network.compressed, IPNetwork(route.source).prefixlen)).compressed
            route.destination = IPNetwork('%s/%s' % (IPNetwork(route.destination).network.compressed, IPNetwork(route.destination).prefixlen)).compressed
            route.requesters_address = request.META['HTTP_X_FORWARDED_FOR']
            route.save()
            form.save_m2m()
            return HttpResponseRedirect(reverse("group-routes"))
        else:
            if not request.user.is_superuser:
                form.fields['then'] = forms.ModelMultipleChoiceField(queryset=ThenAction.objects.filter(action__in=settings.UI_USER_THEN_ACTIONS).order_by('action'), required=True)
                form.fields['protocol'] = forms.ModelMultipleChoiceField(queryset=MatchProtocol.objects.filter(protocol__in=settings.UI_USER_PROTOCOLS).order_by('protocol'), required=False)
            return render_to_response(
                'apply.html',
                {
                    'form': form,
                    'applier': applier
                },
                context_instance=RequestContext(request)
            )


@login_required
@never_cache
def edit_route(request, route_slug):
    applier = request.user.pk
    applier_peer = request.user.get_profile().peer
    route_edit = get_object_or_404(Route, name=route_slug)
    route_edit_applier_peer = route_edit.applier.get_profile().peer
    if applier_peer != route_edit_applier_peer and (not request.user.is_superuser):
        messages.add_message(
            request,
            messages.WARNING,
            ('Insufficient rights to edit rule %s') % (route_slug)
        )
        return HttpResponseRedirect(reverse("group-routes"))
#    if route_edit.status == "ADMININACTIVE" :
#        messages.add_message(request, messages.WARNING,
#                             "Administrator has disabled editing of rule %s" %(route_slug))
#        return HttpResponseRedirect(reverse("group-routes"))
#    if route_edit.status == "EXPIRED" :
#        messages.add_message(request, messages.WARNING,
#                             "Cannot edit the expired rule %s. Contact helpdesk to enable it" %(route_slug))
#        return HttpResponseRedirect(reverse("group-routes"))
    if route_edit.status == 'PENDING':
        messages.add_message(
            request,
            messages.WARNING,
            ('Cannot edit a pending rule: %s.') % (route_slug)
        )
        return HttpResponseRedirect(reverse("group-routes"))
    route_original = deepcopy(route_edit)
    if request.POST:
        request_data = request.POST.copy()
        if request.user.is_superuser:
            request_data['issuperuser'] = request.user.username
        else:
            request_data['applier'] = applier
            try:
                del request_data['issuperuser']
            except:
                pass
        form = RouteForm(
            request_data,
            instance=route_edit
        )
        critical_changed_values = ['source', 'destination', 'sourceport', 'destinationport', 'port', 'protocol', 'then', 'fragmenttype']
        if form.is_valid():
            changed_data = form.changed_data
            route = form.save(commit=False)
            route.name = route_original.name
            route.status = route_original.status
            route.response = route_original.response
            if not request.user.is_superuser:
                route.applier = request.user
            if bool(set(changed_data) & set(critical_changed_values)) or (not route_original.status == 'ACTIVE'):
                route.status = "PENDING"
                route.response = "Applying"
                route.source = IPNetwork('%s/%s' % (IPNetwork(route.source).network.compressed, IPNetwork(route.source).prefixlen)).compressed
                route.destination = IPNetwork('%s/%s' % (IPNetwork(route.destination).network.compressed, IPNetwork(route.destination).prefixlen)).compressed
                route.requesters_address = self.request.META['HTTP_X_FORWARDED_FOR']
            route.save()
            if bool(set(changed_data) & set(critical_changed_values)) or (not route_original.status == 'ACTIVE'):
                form.save_m2m()
                # route.commit_edit()
            return HttpResponseRedirect(reverse("group-routes"))
        else:
            if not request.user.is_superuser:
                form.fields['then'] = forms.ModelMultipleChoiceField(queryset=ThenAction.objects.filter(action__in=settings.UI_USER_THEN_ACTIONS).order_by('action'), required=True)
                form.fields['protocol'] = forms.ModelMultipleChoiceField(queryset=MatchProtocol.objects.filter(protocol__in=settings.UI_USER_PROTOCOLS).order_by('protocol'), required=False)
            return render_to_response(
                'apply.html',
                {
                    'form': form,
                    'edit': True,
                    'applier': applier
                },
                context_instance=RequestContext(request)
            )
    else:
        if (not route_original.status == 'ACTIVE'):
            route_edit.expires = datetime.date.today() + datetime.timedelta(days=settings.EXPIRATION_DAYS_OFFSET)
        dictionary = model_to_dict(route_edit, fields=[], exclude=[])
        if request.user.is_superuser:
            dictionary['issuperuser'] = request.user.username
        else:
            try:
                del dictionary['issuperuser']
            except:
                pass
        form = RouteForm(dictionary)
        if not request.user.is_superuser:
            form.fields['then'] = forms.ModelMultipleChoiceField(queryset=ThenAction.objects.filter(action__in=settings.UI_USER_THEN_ACTIONS).order_by('action'), required=True)
            form.fields['protocol'] = forms.ModelMultipleChoiceField(queryset=MatchProtocol.objects.filter(protocol__in=settings.UI_USER_PROTOCOLS).order_by('protocol'), required=False)
        return render_to_response(
            'apply.html',
            {
                'form': form,
                'edit': True,
                'applier': applier
            },
            context_instance=RequestContext(request)
        )


@login_required
@never_cache
def delete_route(request, route_slug):
    if request.is_ajax():
        route = get_object_or_404(Route, name=route_slug)
        applier_peer = route.applier.get_profile().peer
        requester_peer = request.user.get_profile().peer
        if applier_peer == requester_peer or request.user.is_superuser:
            route.status = "PENDING"
            route.expires = datetime.date.today()
            if not request.user.is_superuser:
                route.applier = request.user
            route.response = "Deactivating"
            route.requesters_address = request.META['HTTP_X_FORWARDED_FOR']
            route.save()
            # route.commit_delete()
        html = "<html><body>Done</body></html>"
        return HttpResponse(html)
    else:
        return HttpResponseRedirect(reverse("group-routes"))


@login_required
@never_cache
def user_profile(request):
    user = request.user
    try:
        peer = request.user.get_profile().peer
        peers = Peer.objects.filter(pk=peer.pk)
        if user.is_superuser:
            peers = Peer.objects.all()
    except UserProfile.DoesNotExist:
        error = "User <strong>%s</strong> does not belong to any peer or organization. It is not possible to create new firewall rules.<br>Please contact Helpdesk to resolve this issue" % user.username
        return render_to_response('error.html', {'error': error}, context_instance=RequestContext(request))
    return render_to_response('profile.html', {'user': user, 'peers':peers},
                                  context_instance=RequestContext(request))

@never_cache
def user_login(request):
    try:
        error_username = False
        error_orgname = False
        error_entitlement = False
        error_mail = False
        has_entitlement = False
        error = ''
        username = lookupShibAttr(settings.SHIB_USERNAME, request.META)
        if not username:
            error_username = True
        firstname = lookupShibAttr(settings.SHIB_FIRSTNAME, request.META)
        lastname = lookupShibAttr(settings.SHIB_LASTNAME, request.META)
        mail = lookupShibAttr(settings.SHIB_MAIL, request.META)
        entitlement = lookupShibAttr(settings.SHIB_ENTITLEMENT, request.META)
        #organization = request.META['HTTP_SHIB_HOMEORGANIZATION']

        if settings.SHIB_AUTH_ENTITLEMENT in entitlement.split(";"):
            has_entitlement = True
        if not has_entitlement:
            error_entitlement = True
#        if not organization:
#            error_orgname = True
        if not mail:
            error_mail = True
        if error_username:
            error = _("Your idP should release the HTTP_EPPN attribute towards this service<br>")
#        if error_orgname:
#            error = error + _("Your idP should release the HTTP_SHIB_HOMEORGANIZATION attribute towards this service<br>")
        if error_entitlement:
            error = error + _("Your idP should release an appropriate HTTP_SHIB_EP_ENTITLEMENT attribute towards this service<br>")
        if error_mail:
            error = error + _("Your idP should release the HTTP_SHIB_INETORGPERSON_MAIL attribute towards this service")
        if error_username or error_orgname or error_entitlement or error_mail:
            return render_to_response('error.html', {'error': error, "missing_attributes": True},
                                  context_instance=RequestContext(request))
        try:
            if settings.SHIB_SLUGIFY_USERNAME:
                username = slugify(username)
            user = User.objects.get(username__exact=username)
            user.email = mail
            user.first_name = firstname
            user.last_name = lastname
            user.save()
            user_exists = True
        except:
            user_exists = False
        user = authenticate(username=username, firstname=firstname, lastname=lastname, mail=mail, authsource='shibboleth')

        if user is not None:
            try:
                peer = user.get_profile().peer
#                peer = Peer.objects.get(domain_name=organization)
#                up = UserProfile.objects.get_or_create(user=user,peer=peer)
            except:
                form = UserProfileForm()
                form.fields['user'] = forms.ModelChoiceField(queryset=User.objects.filter(pk=user.pk), empty_label=None)
                form.fields['peer'] = forms.ModelChoiceField(queryset=Peer.objects.all(), empty_label=None)
                return render_to_response('registration/select_institution.html', {'form': form}, context_instance=RequestContext(request))
            if not user_exists:
                user_activation_notify(user)
            if user.is_active:
               login(request, user)
               return HttpResponseRedirect(reverse("dashboard"))
            else:
                error = _("User account <strong>%s</strong> is pending activation. Administrators have been notified and will activate this account within the next days. <br>If this account has remained inactive for a long time contact your technical coordinator or GRNET Helpdesk") %user.username
                return render_to_response('error.html', {'error': error, 'inactive': True},
                                  context_instance=RequestContext(request))
        else:
            error = _("Something went wrong during user authentication. Contact your administrator")
            return render_to_response('error.html', {'error': error,},
                                  context_instance=RequestContext(request))
    except User.DoesNotExist as e:
        error = _("Invalid login procedure. Error: %s" %e)
        return render_to_response('error.html', {'error': error,},
                                  context_instance=RequestContext(request))
        # Return an 'invalid login' error message.
#    return HttpResponseRedirect(reverse("user-routes"))

def user_activation_notify(user):
    current_site = Site.objects.get_current()
    peer = user.get_profile().peer


    # Email subject *must not* contain newlines
    # TechCs will be notified about new users.
    # Platform admins will activate the users.
    subject = render_to_string('registration/activation_email_subject.txt',
                                   { 'site': current_site })
    subject = ''.join(subject.splitlines())
    registration_profile = RegistrationProfile.objects.create_profile(user)
    message = render_to_string('registration/activation_email.txt',
                                   { 'activation_key': registration_profile.activation_key,
                                     'expiration_days': settings.ACCOUNT_ACTIVATION_DAYS,
                                     'site': current_site,
                                     'user': user })
    if settings.NOTIFY_ADMIN_MAILS:
        admin_mails = settings.NOTIFY_ADMIN_MAILS
        send_new_mail(settings.EMAIL_SUBJECT_PREFIX + subject,
                                  message, settings.SERVER_EMAIL,
                                 admin_mails, [])

    # Mail to domain techCs plus platform admins (no activation hash sent)
    subject = render_to_string('registration/activation_email_peer_notify_subject.txt',
                                   { 'site': current_site,
                                     'peer': peer })
    subject = ''.join(subject.splitlines())
    message = render_to_string('registration/activation_email_peer_notify.txt',
                                   { 'user': user,
                                    'peer': peer })
    send_new_mail(settings.EMAIL_SUBJECT_PREFIX + subject,
                              message, settings.SERVER_EMAIL,
                             get_peer_techc_mails(user), [])

@login_required
@never_cache
def add_rate_limit(request):
    if request.method == "GET":
        form = ThenPlainForm()
        return render_to_response('add_rate_limit.html', {'form': form,},
                                  context_instance=RequestContext(request))

    else:
        form = ThenPlainForm(request.POST)
        if form.is_valid():
            then=form.save(commit=False)
            then.action_value = "%sk"%then.action_value
            then.save()
            response_data = {}
            response_data['pk'] = "%s" %then.pk
            response_data['value'] = "%s:%s" %(then.action, then.action_value)
            return HttpResponse(json.dumps(response_data), mimetype='application/json')
        else:
            return render_to_response('add_rate_limit.html', {'form': form,},
                                      context_instance=RequestContext(request))

@login_required
@never_cache
def add_port(request):
    if request.method == "GET":
        form = PortPlainForm()
        return render_to_response('add_port.html', {'form': form,},
                                  context_instance=RequestContext(request))

    else:
        form = PortPlainForm(request.POST)
        if form.is_valid():
            port=form.save()
            response_data = {}
            response_data['value'] = "%s" %port.pk
            response_data['text'] = "%s" %port.port
            return HttpResponse(json.dumps(response_data), mimetype='application/json')
        else:
            return render_to_response('add_port.html', {'form': form,},
                                      context_instance=RequestContext(request))

@never_cache
def selectinst(request):
    if request.method == 'POST':
        request_data = request.POST.copy()
        user = request_data['user']
        try:
            existingProfile = UserProfile.objects.get(user=user)
            error = _("Violation warning: User account is already associated with an institution.The event has been logged and our administrators will be notified about it")
            return render_to_response('error.html', {'error': error, 'inactive': True},
                                  context_instance=RequestContext(request))
        except UserProfile.DoesNotExist:
            pass

        form = UserProfileForm(request_data)
        if form.is_valid():
            userprofile = form.save()
            user_activation_notify(userprofile.user)
            error = _("User account <strong>%s</strong> is pending activation. Administrators have been notified and will activate this account within the next days. <br>If this account has remained inactive for a long time contact your technical coordinator or GRNET Helpdesk") %userprofile.user.username
            return render_to_response('error.html', {'error': error, 'inactive': True},
                                  context_instance=RequestContext(request))
        else:
            form.fields['user'] = forms.ModelChoiceField(queryset=User.objects.filter(pk=user.pk), empty_label=None)
            form.fields['institution'] = forms.ModelChoiceField(queryset=Peer.objects.all(), empty_label=None)
            return render_to_response('registration/select_institution.html', {'form': form}, context_instance=RequestContext(request))

@never_cache
def overview(request):
    user = request.user
    if user.is_authenticated():
        if user.has_perm('accounts.overview'):
            users = User.objects.all()
            return render_to_response('overview/index.html', {'users': users},
                                  context_instance=RequestContext(request))
        else:
            violation=True
            return render_to_response('overview/index.html', {'violation': violation},
                                  context_instance=RequestContext(request))
    else:
        return HttpResponseRedirect(reverse("altlogin"))

@login_required
@never_cache
def user_logout(request):
    logout(request)
    return HttpResponseRedirect(reverse('group-routes'))

@never_cache
def load_jscript(request, file):
    long_polling_timeout = int(settings.POLL_SESSION_UPDATE)*1000 + 10000
    return render_to_response('%s.js' % file, {'timeout': long_polling_timeout}, context_instance=RequestContext(request), mimetype="text/javascript")


def lookupShibAttr(attrmap, requestMeta):
    for attr in attrmap:
        if (attr in requestMeta.keys()):
            if len(requestMeta[attr]) > 0:
                return requestMeta[attr]
    return ''
