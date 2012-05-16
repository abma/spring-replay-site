# This file is part of the "spring relay site / srs" program. It is published
# under the GPLv3.
#
# Copyright (C) 2012 Daniel Troeder (daniel #at# admin-box #dot# com)
#
#You should have received a copy of the GNU General Public License
#along with this program.  If not, see <http://www.gnu.org/licenses/>.

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render_to_response
from django.core.context_processors import csrf
from django.template import RequestContext
from django.db.models import Count
from django.contrib.auth.decorators import login_required
import django.contrib.auth
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Q
from django.http import Http404
from django.contrib.comments import Comment
from django_tables2 import RequestConfig
from django.db.models import Min


from tempfile import mkstemp
import os
import sets
import shutil
import functools
import locale
import datetime
import logging
import operator

import parse_demo_file
import spring_maps
from models import *
from forms import UploadFileForm, EditReplayForm
from tables import *

logger = logging.getLogger(__package__)

def all_page_infos(request):
    c = {}
    c.update(csrf(request))
    c["total_replays"]   = Replay.objects.count()
    c["top_tags"]        = Tag.objects.annotate(num_replay=Count('replay')).order_by('-num_replay')[:20]
    c["top_maps"]        = Map.objects.annotate(num_replay=Count('replay')).order_by('-num_replay')[:20]
    tp = []
    for pa in PlayerAccount.objects.all():
        tp.append((Player.objects.filter(account=pa, spectator=False).count(), Player.objects.filter(account=pa)[0]))
    tp.sort(key=operator.itemgetter(0), reverse=True)
    c["top_players"] = [p[1] for p in tp[:20]]
    c["latest_comments"] = Comment.objects.reverse()[:5]
    return c

def index(request):
    c = all_page_infos(request)
    c["newest_replays"] = Replay.objects.all().order_by("-pk")[:10]
    c["news"] = NewsItem.objects.all().order_by('-pk')[:10]
    return render_to_response('index.html', c, context_instance=RequestContext(request))

@login_required
def upload(request):
    c = all_page_infos(request)
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            ufile = request.FILES['file']
            short = request.POST['short']
            long_text = request.POST['long_text']
            tags = request.POST['tags']
            (path, written_bytes) = save_uploaded_file(ufile)
            logger.info("User '%s' uploaded file '%s' with title '%s', parsing it now.", request.user, os.path.basename(path), short[:20])
#            try:
            if written_bytes != ufile.size:
                return HttpResponse("Could not store the replay file. Please contact the administrator.")

            demofile = parse_demo_file.Parse_demo_file(path)
            demofile.check_magic()
            demofile.parse()

            try:
                replay = Replay.objects.get(gameID=demofile.header["gameID"])
                logger.info("Replay already existed: pk=%d gameID=%s", replay.pk, replay.gameID)
                return HttpResponse('Uploaded replay already exists: <a href="/replay/%s/">%s</a>'%(replay.gameID, replay.__unicode__()))
            except:
                shutil.move(path, settings.MEDIA_ROOT)
                replay = store_demofile_data(demofile, tags, settings.MEDIA_ROOT+os.path.basename(path), file.name, short, long_text, request.user)
                logger.info("New replay created: pk=%d gameID=%s", replay.pk, replay.gameID)
            return HttpResponseRedirect(replay.get_absolute_url())
#            except Exception, e:
#                return HttpResponse("The was a problem with the upload: %s<br/>Please retry or contact the administrator.<br/><br/><a href="/">Home</a>"%e)
    else:
        form = UploadFileForm()
    c['form'] = form
    return render_to_response('upload.html', c, context_instance=RequestContext(request))

def replays(request):
    c = all_page_infos(request)
    table = ReplayTable(Replay.objects.all())
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "replays"
    c['long_table'] = True
    return render_to_response('lists.html', c, context_instance=RequestContext(request))

def replay(request, gameID):
    c = all_page_infos(request)
    try:
        c["replay"] = Replay.objects.prefetch_related().get(gameID=gameID)
    except:
        # TODO: nicer error handling
        raise Http404

    c["allyteams"] = []
    for at in Allyteam.objects.filter(replay=c["replay"]):
        teams = Team.objects.prefetch_related("teamleader").filter(allyteam=at, replay=c["replay"])
        if teams:
            c["allyteams"].append((at, teams))
    c["specs"] = Player.objects.filter(replay=c["replay"], spectator=True)

    return render_to_response('replay.html', c, context_instance=RequestContext(request))

@login_required
def edit_replay(request, gameID):
    c = all_page_infos(request)
    try:
        replay = Replay.objects.prefetch_related().get(gameID=gameID)
        c["replay"] = replay
    except:
        # TODO: nicer error handling
        raise Http404

    if request.user != replay.uploader:
        return render_to_response('edit_replay_wrong_user.html', c, context_instance=RequestContext(request))

    if request.method == 'POST':
        form = EditReplayForm(request.POST)
        if form.is_valid():
            short = request.POST['short']
            long_text = request.POST['long_text']
            tags = request.POST['tags']

            replay.tags.clear()
            autotag = set_autotag(replay)
            save_tags(replay, tags)
            save_desc(replay, short, long_text, autotag)
            replay.save()
            logger.info("User '%s' modified replay '%s': short: '%s' title:'%s' long_text:'%s' tags:'%s'",
                        request.user, replay.gameID, replay.short_text, replay.title, replay.long_text, reduce(lambda x,y: x+", "+y, [t.name for t in Tag.objects.filter(replay=replay)]))
            return HttpResponseRedirect(replay.get_absolute_url())
    else:
        form = EditReplayForm({'short': replay.short_text, 'long_text': replay.long_text, 'tags': reduce(lambda x,y: x+", "+y, [t.name for t in Tag.objects.filter(replay=replay)])})
    c['form'] = form

    return render_to_response('edit_replay.html', c, context_instance=RequestContext(request))

def download(request, gameID):
    # TODO
    c = all_page_infos(request)
    try:
        rf = Replay.objects.get(gameID=gameID).replayfile
    except:
        # TODO: nicer error handling
        raise Http404

    rf.download_count += 1
    rf.save()
    return HttpResponseRedirect(settings.STATIC_URL+"replays/"+rf.filename)

def tags(request):
    c = all_page_infos(request)
    table = TagTable(Tag.objects.all())
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "tags"
    return render_to_response('lists.html', c, context_instance=RequestContext(request))

def tag(request, reqtag):
    # TODO
    c = all_page_infos(request)
    rep = "<b>TODO</b><br/><br/>list of games with tag '%s':<br/>"%reqtag
    for replay in Replay.objects.filter(tags__name=reqtag):
        rep += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def maps(request):
    c = all_page_infos(request)
    table = MapTable(Map.objects.all())
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "maps"
    return render_to_response('lists.html', c, context_instance=RequestContext(request))

def rmap(request, mapname):
    # TODO
    c = all_page_infos(request)
    rep = "<b>TODO</b><br/><br/>list of games on map '%s':<br/>"%mapname
    for replay in Replay.objects.filter(map_info__name=mapname):
        rep += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def players(request):
    c = all_page_infos(request)
    players = []
    for pa in PlayerAccount.objects.all():
        for name in pa.names.split(";"):
            players.append({'name': name,
                            'replay_count': pa.replay_count(),
                            'spectator_count': pa.spectator_count(),
                            'accid': pa.accountid})
    table = PlayerTable(players)
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "players"
    return render_to_response('lists.html', c, context_instance=RequestContext(request))

def player(request, accountid):
    # TODO
    c = all_page_infos(request)
    rep = "<b>TODO</b><br/><br/>This player is know as:<br/>"
    accounts = []

    try:
        accounts.append(PlayerAccount.objects.get(accountid=accountid))
    except:
        # TODO: nicer error handling
        raise Http404
    accounts.extend(PlayerAccount.objects.filter(aka=accounts[0]))
    for account in accounts:
        for a in account.names.split(";"):
            rep += '* %s<br/>'%a

    players = Player.objects.select_related("replay").filter(account__in=accounts)
    rep += "<br/><br/>This player (with one of his accounts/aliases) has played in these games:<br/>"
    for player in players:
        rep += '* <a href="%s">%s</a>'%(player.replay.get_absolute_url(), player.replay.__unicode__())
        if player.spectator:
            rep += ' (spec)'
        rep += '<br/>'
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def games(request):
    c = all_page_infos(request)
    games = []
    for gt in list(set(Replay.objects.values_list('gametype', flat=True))):
        games.append({'name': gt,
                      'replays': Replay.objects.filter(gametype=gt).count()})
    table = GameTable(games)
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "games"
    return render_to_response('lists.html', c, context_instance=RequestContext(request))
def game(request, gametype):
    # TODO
    c = all_page_infos(request)
    rep = "<b>TODO</b><br/><br/>list of replays of game %s:<br/>"%gametype
    for replay in Replay.objects.filter(gametype=gametype):
        rep += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def search(request):
    # TODO
    c = all_page_infos(request)
    resp = "<b>TODO</b><br/><br/>"
    if request.method == 'POST':
        st = request.POST["search"].strip()
        if st:
            users = User.objects.filter(username__icontains=st)
            replays = Replay.objects.filter(Q(gametype__icontains=st)|
                                            Q(title__icontains=st)|
                                            Q(short_text__icontains=st)|
                                            Q(long_text__icontains=st)|
                                            Q(map_info__name__icontains=st)|
                                            Q(tags__name__icontains=st)|
                                            Q(uploader__in=users)|
                                            Q(player__account__names__icontains=st)).distinct()

            resp += 'Your search for "%s" yielded %d results:<br/><br/>'%(st, replays.count())
            for replay in replays:
                resp += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
        else:
            HttpResponseRedirect("/search/")
    return HttpResponse(resp)

@login_required
def user_settings(request):
    # TODO:
    c = all_page_infos(request)
    return render_to_response('settings.html', c, context_instance=RequestContext(request))

def users(request):
    c = all_page_infos(request)
    table = UserTable(User.objects.all())
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "users"
    return render_to_response('lists.html', c, context_instance=RequestContext(request))

def see_user(request, username):
    # TODO:
    rep = "<b>TODO</b><br/><br/>"
    user = User.objects.get(username=username)
    try:
        rep += "list of replays uploaded by %s:<br/>"%username
        for replay in Replay.objects.filter(uploader=user):
            rep += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
    except:
        rep += "user %s unknown.<br/>"%username
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def match_date(request, shortdate):
    # TODO:
    rep = "<b>TODO</b><br/><br/>"
    rep += "list of replays played on %s:<br/>"%shortdate
    for replay in Replay.objects.filter(unixTime__startswith=shortdate):
        rep += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def upload_date(request, shortdate):
    # TODO:
    rep = "<b>TODO</b><br/><br/>"
    rep += "list of replays uploaded on %s:<br/>"%shortdate
    for replay in Replay.objects.filter(upload_date__startswith=shortdate):
        rep += '* <a href="%s">%s</a><br/>'%(replay.get_absolute_url(), replay.__unicode__())
    rep += '<br/><br/><a href="/">Home</a>'
    return HttpResponse(rep)

def all_comments(request):
    c = all_page_infos(request)
    table = CommentTable(Comment.objects.all())
    RequestConfig(request, paginate={"per_page": 50}).configure(table)
    c['table'] = table
    c['pagetitle'] = "comments"
    c['long_table'] = True
    return render_to_response('lists.html', c, context_instance=RequestContext(request))

def login(request):
    c = all_page_infos(request)
    if request.method == 'POST':
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = django.contrib.auth.authenticate(username=form.cleaned_data["username"], password=form.cleaned_data["password"])
            if user is not None:
                if user.is_active:
                    django.contrib.auth.login(request, user)
                    logger.info("Logged in user '%s'", request.user)
                    nexturl = request.GET.get('next')
                    # TODO: "next" is never passed...
                    if nexturl:
                        dest = nexturl
                    else:
                        dest = "/"
                    return HttpResponseRedirect(dest)
    else:
        form = AuthenticationForm()
    c['form'] = form
    return render_to_response('login.html', c, context_instance=RequestContext(request))

def logout(request):
    username = str(request.user)
    django.contrib.auth.logout(request)
    logger.info("Logged out user '%s'", username)
    return HttpResponseRedirect("/")

def xmlrpc_upload(username, password, filename, demofile, subject, comment, tags, owner):
    logger.info("username=%s password=xxxxxx filename=%s subject=%s comment=%s tags=%s owner=%s", username, filename, subject, comment, tags, owner)

    # authenticate uploader
    user = django.contrib.auth.authenticate(username=username, password=password)
    if user is not None and user.is_active:
        logger.info("Authenticated user '%s'", user)
    else:
        logger.info("Uploader account unknown or inactive, abort.")
        return "1 Unknown or inactive uploader account."

    # find owner account
    try:
        ac = max([pa.accountid for pa in PlayerAccount.objects.filter(player__name=owner)])
        owner_pa = PlayerAccount.objects.get(accountid=ac)
        logger.info("Owner is '%s'", owner_pa)
    except:
        logger.info("Owner unknown on replays site, abort.")
        return "2 Unknown or inactive owner account, please log in via web interface once."

    # this is code double from upload() :(
    (fd, path) = mkstemp(suffix=".sdf", prefix=filename[:-4]+"__")
    bytes_written = os.write(fd, demofile.data)
    logger.debug("wrote %d bytes to /tmp/%s", bytes_written, filename)

    demofile = parse_demo_file.Parse_demo_file(path)
    demofile.check_magic()
    demofile.parse()

    try:
        replay = Replay.objects.get(gameID=demofile.header["gameID"])
        logger.info("Replay already existed: pk=%d gameID=%s", replay.pk, replay.gameID)
        return '3 uploaded replay already exists as "%s" at "%s"'%(replay.__unicode__(), replay.get_absolute_url())
    except:
        pass

    shutil.move(path, settings.MEDIA_ROOT)
    try:
        replay = store_demofile_data(demofile, tags, settings.MEDIA_ROOT+os.path.basename(path), filename, subject, comment, user)
        replay.uploader = owner_pa
        replay.save()
    except Exception, e:
        logger.error("Error in store_demofile_data(): %s", e)
        return "4 server error, please try again later, or contact admin"

    logger.info("New replay created: pk=%d gameID=%s", replay.pk, replay.gameID)
    return '0 received %d bytes, replay at "%s"'%(bytes_written, replay.get_absolute_url())

###############################################################################
###############################################################################

def save_uploaded_file(ufile):
    """
    may raise an exception from os.open/write/close()
    """
    (fd, path) = mkstemp(suffix=".sdf", prefix=ufile.name[:-4]+"__")
    written_bytes = 0
    for chunk in ufile.chunks():
        written_bytes += os.write(fd, chunk)
    os.close(fd)
    logger.debug("stored file with '%d' bytes in '%s'", written_bytes, path)
    return (path, written_bytes)

def store_demofile_data(demofile, tags, path, filename, short, long_text, user):
    """
    Store all data about this replay in the database
    """
    replay = Replay()
    replay.uploader = user

    # copy match infos 
    for key in ["versionString", "gameID", "wallclockTime"]:
        replay.__setattr__(key, demofile.header[key])
    replay.unixTime = datetime.datetime.strptime(demofile.header["unixTime"], "%Y-%m-%d %H:%M:%S")
    for key in ["mapname", "autohostname", "gametype", "startpostype"]:
        if demofile.game_setup["host"].has_key(key):
            replay.__setattr__(key, demofile.game_setup["host"][key])

    # the replay file
    replay.replayfile = ReplayFile.objects.create(filename=os.path.basename(path), path=os.path.dirname(path), ori_filename=filename, download_count=0)

    replay.save()

    logger.debug("replay pk=%d gameID=%s unixTime=%s created", replay.pk, replay.gameID, replay.unixTime)

    # save AllyTeams
    allyteams = {}
    for num,val in demofile.game_setup['allyteam'].items():
        allyteam = Allyteam()
        allyteams[num] = allyteam
        for k,v in val.items():
            allyteam.__setattr__(k, v)
        allyteam.replay = replay
        allyteam.winner = int(num) in demofile.winningAllyTeams
        allyteam.save()

    logger.debug("replay pk=%d allyteams=%s", replay.pk, [a.pk for a in allyteams.values()])

    # winner known?
    replay.notcomplete = demofile.header['winningAllyTeamsSize'] == 0

    # get / create map infos
    try:
        replay.map_info = Map.objects.get(name=demofile.game_setup["host"]["mapname"])
        logger.debug("replay pk=%d using existing map_info.pk=%d", replay.pk, replay.map_info.pk)
    except:
        # 1st time upload for this map: fetch info and full map, create thumb
        # for index page
        smap = spring_maps.Spring_maps(demofile.game_setup["host"]["mapname"])
        smap.fetch_info()
        startpos = ""
        for coord in smap.map_info[0]["metadata"]["StartPos"]:
            startpos += "%f,%f|"%(coord["x"], coord["z"])
        startpos = startpos[:-1]
        replay.map_info = Map.objects.create(name=demofile.game_setup["host"]["mapname"], startpos=startpos, height=smap.map_info[0]["metadata"]["Height"], width=smap.map_info[0]["metadata"]["Width"])

        full_img = smap.fetch_img()
        MapImg.objects.create(filename=full_img, startpostype=-1, map_info=replay.map_info)
        smap.make_home_thumb()
        logger.debug("replay pk=%d created new map_info and MapImg: map_info.pk=%d", replay.pk, replay.map_info.pk)

    # get / create map thumbs
    logger.debug("replay pk=%d startpostype=%d", replay.pk, demofile.game_setup["host"]["startpostype"])
    if demofile.game_setup["host"]["startpostype"] == 1:
        # fixed start positions before game
        try:
            replay.map_img = MapImg.objects.get(map_info = replay.map_info, startpostype=1)
            logger.debug("replay pk=%d using existing map_img.pk=%d", replay.pk, replay.map_img.pk)
        except:
            mapfile = spring_maps.create_map_with_positions(replay.map_info)
            replay.map_img = MapImg.objects.create(filename=mapfile, startpostype=1, map_info=replay.map_info)
            logger.debug("replay pk=%d created new map_img.pk=%d", replay.pk, replay.map_img.pk)
    elif demofile.game_setup["host"]["startpostype"] == 2:
        # start boxes
        mapfile = spring_maps.create_map_with_boxes(replay)
        replay.map_img = MapImg.objects.create(filename=mapfile, startpostype=2, map_info=replay.map_info)
        logger.debug("replay pk=%d created new map_img.pk=%d", replay.pk, replay.map_img.pk)
    else:
        #TODO:
        logger.debug("replay pk=%d startpostype not yet supported", replay.pk)
        raise Exception("startpostype not yet supported, pls report this to dansan at the forums and include replay file")

    replay.save()

    # save tags
    save_tags(replay, tags)
    replay.save()

    # save map and mod options
    for k,v in demofile.game_setup['mapoptions'].items():
        MapOption.objects.create(name=k, value=v, replay=replay)

    for k,v in demofile.game_setup['modoptions'].items():
        ModOption.objects.create(name=k, value=v, replay=replay)

    logger.debug("replay pk=%d added tags, mapoptions and modoptions", replay.pk)

    # save players and their accounts
    players = {}
    teams = {}
    for k,v in demofile.game_setup['player'].items():
        pac = Player.objects.none()
        if v.has_key("accountid"):
            # check if we have a Player that was missing an accountid previously
            pac = Player.objects.filter(name=v["name"], account__accountid__lt=0)
        else:
            # single player - we still need a unique accountid. I make it
            # negative, so if the same Player pops up in another replay, and has
            # a proper accountid, it can be noticed and corrected
            min_acc_id = PlayerAccount.objects.aggregate(Min("accountid"))['accountid__min']
            if not min_acc_id or min_acc_id > 0: min_acc_id = 0
            v["accountid"] = min_acc_id-1
        if v.has_key("lobbyid"):
            # game was on springie
            v["accountid"] = v["lobbyid"]
        pa, created = PlayerAccount.objects.get_or_create(accountid=v["accountid"], defaults={'accountid': v["accountid"], 'countrycode': v["countrycode"], 'names': v["name"]})
        logger.debug("replay pk=%d PlayerAccount: created=%s pa.pk=%d pa.accountid=%d pa.names=%s", replay.pk, created, pa.pk, pa.accountid, pa.names)
        players[k] = Player.objects.create(account=pa, name=v["name"], rank=v["rank"], spectator=bool(v["spectator"]), replay=replay)
        logger.debug("replay pk=%d created Player: account=%d name=%s spectator=%s", replay.pk, pa.accountid, players[k].name, players[k].spectator)
        if not created:
            # add players name to accounts aliases
            if v["name"] not in pa.names.split(";"):
                pa.names += ";"+v["name"]
                pa.save()
        if pa.accountid > 0:
            # if we found players w/o account, and now have a player with the
            # same name, but with an account - unify them
            if pac:
                logger.info("replay pk=%d found matching name-account info for previously accountless player(s):", replay.pk)
                logger.info("replay pk=%d PA.pk=%d Player(s).pk:%s", replay.pk, pa.pk, [(p.name, p.pk) for p in pac])
            for player in pac:
                old_ac = player.account
                player.account = pa
                player.save
                old_ac.delete()
        if v.has_key("team"):
            teams[str(v["team"])] = players[k]
    logger.debug("replay pk=%d saved Players and PlayerAccounts", replay.pk,)

    # save teams
    for num,val in demofile.game_setup['team'].items():
        team = Team()
        for k,v in val.items():
            if k == "allyteam":
                team.allyteam = allyteams[str(v)]
            elif k == "teamleader":
                team.teamleader = players[str(v)]
            elif k =="rgbcolor":
                team.rgbcolor = floats2rgbhex(v)
            else:
                team.__setattr__(k, v)
        team.replay = replay
        team.save()

        teams[num].team = team # Player.team
        teams[num].save()
    logger.debug("replay pk=%d saved Teams", replay.pk)

    # work around Zero-Ks usage of useless AllyTeams
    ats = Allyteam.objects.filter(replay=replay, team__isnull=True)
    logger.debug("replay pk=%d deleting useless AllyTeams:%s", replay.pk, ats)
    ats.delete()

    # auto add tag 1v1 2v2 etc.
    autotag = set_autotag(replay)

    # TODO: SP and bot detection

    # save descriptions
    save_desc(replay, short, long_text, autotag)

    replay.save()
    logger.debug("replay pk=%d autotag='%s', title='%s'", replay.pk, tag, replay.title)

    return replay

def save_tags(replay, tags):
    if tags:
        # strip comma separated tags and remove empty ones
        tags_ = [t.strip() for t in tags.split(",") if t.strip()]
        for tag in tags_:
            t_obj, _ = Tag.objects.get_or_create(name__iexact = tag, defaults={'name': tag})
            replay.tags.add(t_obj)

def set_autotag(replay):
    autotag = ""
    if Allyteam.objects.filter(replay=replay).count() > 3:
        autotag = "FFA"
    else:
        for at in Allyteam.objects.filter(replay=replay):
            autotag += str(Team.objects.filter(allyteam=at).count())+"v"
        autotag = autotag[:-1]

    tag, created = Tag.objects.get_or_create(name = autotag, defaults={'name': autotag})
    if created:
        replay.tags.add(tag)
    else:
        if not replay.tags.filter(name=autotag).exists():
            replay.tags.add(tag)
    return autotag

def save_desc(replay, short, long_text, autotag):
    replay.short_text = short
    replay.long_text = long_text
    if autotag in short:
        replay.title = short
    else:
        replay.title = autotag+" "+short

def clamp(val, low, high):
    return min(high, max(low, val))

def floats2rgbhex(floats):
    rgb = ""
    for color in floats.split():
        rgb += str(hex(clamp(int(float(color)*256), 0, 256))[2:])
    return rgb
