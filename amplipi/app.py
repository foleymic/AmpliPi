#!/usr/bin/python3

# AmpliPi Home Audio
# Copyright (C) 2021 MicroNova LLC
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""AmpliPi Webapp

This serves the amplipi webpp and underlying rest api, that it uses.
The FastAPI/Starlette web framework is used to simplify the web plumbing.
"""

# web framework
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Path
from fastapi.staticfiles import StaticFiles
from fastapi.routing import APIRoute, APIRouter
from starlette.responses import FileResponse
from fastapi.templating import Jinja2Templates
from fastapi_utils.cbv import cbv
# type handling, fastapi leverages type checking for performance and easy docs
from typing import List, Optional, Dict, Union, Set
from functools import lru_cache
#docs
import argparse
from fastapi.openapi.utils import get_openapi
import yaml
import io
import json

# web server
import uvicorn
# amplipi
from amplipi.ctrl import Api # we don't import ctrl here to avoid naming ambiguity with a ctrl variable
import amplipi.rt as rt
import amplipi.utils as utils
import amplipi.models as models

# start in the web directory
import os
template_dir = os.path.abspath('web/templates')
static_dir = os.path.abspath('web/static')
generated_dir = os.path.abspath('web/generated')

app = FastAPI(openapi_url=None, redoc_url=None,) # we host docs using rapidoc instead via a custom endpoint, so the default endpoints need to be disabled
templates = Jinja2Templates(template_dir)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/generated", StaticFiles(directory=generated_dir), name="generated") # TODO: make this register as a dynamic folder???

from typing import TYPE_CHECKING, Any, Callable, get_type_hints
from fastapi import APIRouter

class SimplifyingRouter(APIRouter):
  """
  Overrides the route decorator logic to:
  - to use the annotated return type as the `response_model` if unspecified.
  - always exclude unset fields (this makes so much more sense!)
  """
  if not TYPE_CHECKING:  # pragma: no branch
    def add_api_route(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
      if kwargs.get("response_model") is None:
        kwargs["response_model"] = get_type_hints(endpoint).get("return")
      kwargs["response_model_exclude_none"] = True
      return super().add_api_route(path, endpoint, **kwargs)

# Helper functions
def unused_groups(src: int) -> Dict[int,str]:
  """ Get groups that are not connected to src """
  ctrl = get_ctrl()
  groups = ctrl.status.groups
  return { g.id : g.name for g in groups if g.source_id != src}

def unused_zones(src: int) -> Dict[int,str]:
  """ Get zones that are not conencted to src """
  ctrl = get_ctrl()
  zones = ctrl.status.zones
  return { z.id : z.name for z in zones if z.source_id != src }

def ungrouped_zones(src: int) -> List[models.Zone]:
  """ Get zones that are connected to src, but don't belong to a full group """
  ctrl = get_ctrl()
  zones = ctrl.status.zones
  groups = ctrl.status.groups
  # get all of the zones that belong to this sources groups
  grouped_zones: Set[int] = set()
  for g in groups:
    if g.source_id == src:
      grouped_zones = grouped_zones.union(g.zones)
  # get all of the zones connected to this soource
  source_zones = set([ z.id for z in zones if z.source_id == src ])
  # return all of the zones connected to this source that aren't in a group
  ungrouped_zones_ = source_zones.difference(grouped_zones)
  return [ zones[z] for z in ungrouped_zones_ if z and not zones[z].disabled]

def song_info(src: int) -> Dict[str,str]:
  """ Get the song info for a source """
  ctrl =  get_ctrl()
  song_fields = ['artist', 'album', 'track', 'img_url']
  stream = ctrl._get_stream(src)
  info = stream.info if stream else {}
  # add empty strings for unpopulated fields
  for field in song_fields:
    if field not in info:
      info[field] = ''
  return info

# add a default controller (this is overriden below in create_app)
@lru_cache(1) # Api controller should only be instantiated once (we clear the cache with get_ctr.cache_clear() after settings object is configured)
def get_ctrl() -> Api:
  return Api(models.AppSettings())

class params(object):
  """ Describe standard path ID's for each api type """
  SourceID = Path(..., ge=0, le=3, description="Source ID")
  ZoneID = Path(..., ge=0, le=35, description="Zone ID")
  GroupID = Path(..., ge=0, description="Stream ID")
  StreamID = Path(..., ge=0, description="Stream ID")
  StreamCommand = Path(..., description="Stream Command")
  PresetID = Path(..., ge=0, description="Preset ID")
  StationID = Path(..., ge=0, title="Pandora Station ID", description="Number found on the end of a pandora url while playing the station, ie 4610303469018478727 in https://www.pandora.com/station/play/4610303469018478727")

# Add our own router and class endpoints to reduce boilerplate for our api
api_router = SimplifyingRouter()
base_router = SimplifyingRouter()
@cbv(api_router)
class ApiRoutes:

  # embedded ctrl dependency used by every api route
  ctrl: Api = Depends(get_ctrl)

  @api_router.get('/', tags=['status'])
  def get_status(self) -> models.Status:
    """ Get the system status and configuration """
    return self.ctrl.get_state()

  def code_response(self, resp):
    if resp is None:
      # general commands return None to indicate success
      return self.ctrl.get_state()
    elif 'error' in resp:
      # TODO: refine error codes based on error message
      raise HTTPException(404, resp['error'])
    else:
      return resp

  # sources

  @api_router.get('/sources', tags=['source'])
  def get_sources(self) -> Dict[str, List[models.Source]]:
    """ Get all sources """
    return {'sources' : self.ctrl.get_state().sources}

  @api_router.get('/sources/{sid}', tags=['source'])
  def get_source(self, sid: int=params.SourceID) -> models.Source:
    """ Get Source with id=**sid** """
    # TODO: add get_X capabilities to underlying API?
    sources = self.ctrl.get_state().sources
    return sources[sid]

  @api_router.patch('/sources/{sid}', tags=['source'])
  def set_source(self, sid: int = params.SourceID, update: models.SourceUpdate=None) -> models.Status:
    """ Update a source's configuration (source=**sid**) """
    return self.code_response(self.ctrl.set_source(sid, update))

  # zones

  @api_router.get('/zones', tags=['zone'])
  def get_zones(self) -> Dict[str, List[models.Zone]]:
    """ Get all zones """
    return {'zones': self.ctrl.get_state().zones}

  @api_router.get('/zones/{zid}', tags=['zone'])
  def get_zone(self, zid: int=params.ZoneID) -> models.Zone:
    """ Get Zone with id=**zid** """
    zones = self.ctrl.get_state().zones
    if zid >= 0 and zid < len(zones):
      return zones[zid]
    else:
      raise HTTPException(404, f'zone {zid} not found')

  @api_router.patch('/zones/{zid}', tags=['zone'])
  def set_zone(self, zid: int=params.ZoneID, zone: models.ZoneUpdate=None) -> models.Status:
    """ Update a zone's configuration (zone=**zid**) """
    return self.code_response(self.ctrl.set_zone(zid, zone))

  # TODO: add set_zones(self, zones:List[int]=[], groups:List[int]=[], update:ZoneUpdate)

  # groups

  @api_router.post('/group', tags=['group'])
  def create_group(self, group: models.Group) -> models.Group:
    """ Create a new grouping of zones """
    # TODO: add named example group
    return self.code_response(self.ctrl.create_group(group))

  @api_router.get('/groups', tags=['group'])
  def get_groups(self) -> Dict[str, List[models.Group]]:
    """ Get all groups """
    return {'groups' : self.ctrl.get_state().groups}

  @api_router.get('/groups/{gid}', tags=['group'])
  def get_group(self, gid: int=params.GroupID) -> models.Group:
    """ Get Group with id=**gid** """
    _, grp = utils.find(self.ctrl.get_state().groups, gid)
    if grp is not None:
      return grp
    else:
      raise HTTPException(404, f'group {gid} not found')

  @api_router.patch('/groups/{gid}', tags=['group'])
  def set_group(self, gid: int=params.GroupID, group: models.GroupUpdate=None) -> models.Status:
    """ Update a groups's configuration (group=**gid**) """
    return self.code_response(self.ctrl.set_group(gid, group)) # TODO: pass update directly

  @api_router.delete('/groups/{gid}', tags=['group'])
  def delete_group(self, gid: int=params.GroupID) -> models.Status:
    """ Delete a group (group=**gid**) """
    return self.code_response(self.ctrl.delete_group(id=gid))

  # streams

  @api_router.post('/stream', tags=['stream'])
  def create_stream(self, stream: models.Stream) -> models.Stream:
    """ Create a new audio stream """
    # TODO: add an example stream for each type of stream
    return self.code_response(self.ctrl.create_stream(stream))

  @api_router.get('/streams', tags=['stream'])
  def get_streams(self) -> Dict[str, List[models.Stream]]:
    """ Get all streams """
    return {'streams' : self.ctrl.get_state().streams}

  @api_router.get('/streams/{sid}', tags=['stream'])
  def get_stream(self, sid: int=params.StreamID) -> models.Stream:
    """ Get Stream with id=**sid** """
    _, stream = utils.find(self.ctrl.get_state().streams, sid)
    if stream is not None:
      return stream
    else:
      raise HTTPException(404, f'stream {sid} not found')

  @api_router.patch('/streams/{sid}', tags=['stream'])
  def set_stream(self, sid: int=params.StreamID, update: models.StreamUpdate=None) -> models.Status:
    """ Update a stream's configuration (stream=**sid**) """
    return self.code_response(self.ctrl.set_stream(sid, update))

  @api_router.delete('/streams/{sid}', tags=['stream'])
  def delete_stream(self, sid: int=params.StreamID) -> models.Status:
    """ Delete a stream """
    return self.code_response(self.ctrl.delete_stream(sid))


  @api_router.post('/streams/{sid}/station={station}', tags=['stream'])
  def change_station(self, sid: int=params.StreamID, station: int=params.StationID) -> models.Status:
    """ Change station on a pandora stream (stream=**sid**) """
    # This is a specific version of exec command, it needs to be placed before the genertic version so the path is resolved properly
    return self.code_response(self.ctrl.exec_stream_command(sid, cmd=f'station={station}'))

  @api_router.post('/streams/{sid}/{cmd}', tags=['stream'])
  def exec_command(self, sid: int=params.StreamID, cmd: models.StreamCommand=None) -> models.Status:
    """ Execute a comamnds on a stream (stream=**sid**).

      Command options:
      * Play Stream: **play**
      * Pause Stream: **pause**
      * Skip to next song: **next**
      * Stop Stream: **stop**
      * Like/Love Current Song: **love**
      * Ban Current Song (pandora only): **ban**
      * Shelve Current Song (pandora only): **shelve**

    Currently only available with Pandora streams"""
    return self.code_response(self.ctrl.exec_stream_command(sid, cmd=cmd))

  # presets

  @api_router.post('/preset', tags=['preset'])
  def create_preset(self, preset: models.Preset) -> models.Preset:
    """ Create a new preset configuration """
    return self.code_response(self.ctrl.create_preset(preset))

  @api_router.get('/presets', tags=['preset'])
  def get_presets(self) -> Dict[str, List[models.Preset]]:
    """ Get all presets """
    return {'presets' : self.ctrl.get_state().presets}

  @api_router.get('/presets/{pid}', tags=['preset'])
  def get_preset(self, pid: int=params.PresetID) -> models.Preset:
    """ Get Preset with id=**pid** """
    _, preset = utils.find(self.ctrl.get_state().presets, pid)
    if preset is not None:
      return preset
    else:
      raise HTTPException(404, f'preset {pid} not found')

  @api_router.patch('/presets/{pid}', tags=['preset'])
  async def set_preset(self, pid: int=params.PresetID, update: models.PresetUpdate=None) -> models.Status:
    """ Update a preset's configuration (preset=**pid**) """
    return self.code_response(self.ctrl.set_preset(pid, update))

  @api_router.delete('/presets/{pid}', tags=['preset'])
  def delete_preset(self, pid: int=params.PresetID) -> models.Status:
    """ Delete a preset """
    return self.code_response(self.ctrl.delete_preset(pid))

  @api_router.post('/presets/{pid}/load', tags=['preset'])
  def load_preset(self, pid: int=params.PresetID) -> models.Status:
    """ Load a preset configuration """
    return self.code_response(self.ctrl.load_preset(pid))

  # Documentation

  @api_router.get('/doc', include_in_schema=False)
  def doc(self):
    # TODO: add hosted python docs as well
    return FileResponse(f'{template_dir}/rest-api-doc.html') # TODO: this is not really a template

# add the root of the API as well, since empty paths are invalid this needs to be handled outside of the router
@base_router.get('/api', tags=['status'])
def get_status(ctrl: Api = Depends(get_ctrl)) -> models.Status:
  """ Get the system status and configuration """
  return ctrl.get_state()

app.include_router(base_router)
app.include_router(api_router, prefix='/api')

# API Documentation
def generate_openapi_spec(add_test_docs=True):
  if app.openapi_schema:
    return app.openapi_schema
  openapi_schema = get_openapi(
    title = 'AmpliPi',
    version = '1.0',
    description = "This is the AmpliPi home audio system's control server.",
    routes = app.routes,
    tags = [
      {
        'name': 'status',
        'description': 'The status and configuration of the entire system, including source, zones, groups, and streams.',
      },
      {
        'name': 'source',
        'description': 'Audio source. Can accept sudio input from a local (RCA) connection or any stream. Sources can be connected to one or multiple zones, or connected to nothing at all.',
      },
      {
        'name': 'zone',
        'description': 'Stereo output to a set of speakers, typically a room. Individually controllable with its own volume control. Can be connected to one of the 4 audio sources.',
      },
      {
        'name': 'group',
        'description': '''Group of zones. Grouping allows a set of zones to be controlled together. A zone can belong to multiple groups, allowing for different levels of abstraction, ie. Guest Bedroom can belong to both the 'Upstairs' and 'Whole House' groups.,'''
      },
      {
        'name': 'stream',
        'description': 'Digital stream that can be connected to a source, ie. Pandora, Airplay, Spotify, Internet Radio, DLNA.',
      },
      {
        'name': 'preset',
        'description': '''A partial system configuration. Used to load specific configurations, such as "Home Theater" mode where the living room speakers are connected to the TV's audio output.''',
      }
    ],
    servers = [{
      'url': '',
      'description': 'AmpliPi Controller'
    }],
  )

  openapi_schema['info']['contact'] = {
    'email': 'info@micro-nova.com',
    'name':  'Micronova',
    'url':   'http://micro-nova.com',
  }
  openapi_schema['info']['license'] = {
    'name': 'GPL',
    'url':  '/license',
  }

  # Manually add examples present in pydancticModel.schema_extra into openAPI schema
  for route in app.routes:
    if isinstance(route, APIRoute):
      try:
        if route.body_field:
          req_model_json = route.body_field.type_.schema_json()
        else:
          req_model_json = ''
      except:
        req_model_json = ''
      try:
        if route.response_field:
          resp_model_json = route.response_field.type_.schema_json()
        else:
          resp_model_json = ''
      except:
        resp_model_json = ''
      if req_model_json:
        req_model = json.loads(req_model_json)
        if "examples" in req_model or 'creation_examples' in req_model:
          if 'creation_examples' in req_model: # prefer creation examples for POST request, this allows us to have different examples for get response and creation requests
            examples = req_model['creation_examples']
          else:
            examples = req_model['examples']
          for method in route.methods:
            # Only POST, PATCH, and PUT methods have a request body
            if method in {"POST", "PATCH", "PUT"}:
              openapi_schema['paths'][route.path][method.lower()]['requestBody'][
              'content']['application/json']['examples'] = examples
      if resp_model_json:
        resp_model = json.loads(resp_model_json)
        if 'examples' in resp_model:
          examples = resp_model['examples']
          for method in route.methods:
            openapi_schema['paths'][route.path][method.lower()]['responses']['200'][
              'content']['application/json']['examples'] = examples
      if route.path in ['/api/zones', '/api/groups', '/api/sources', '/api/streams', '/api/presets']:
        if 'get' in  openapi_schema['paths'][route.path]:
          piece = route.path.replace('/api/', '')
          example_status = list(models.Status.Config.schema_extra['examples'].values())[0]['value']
          openapi_schema['paths'][route.path]['get']['responses']['200'][
              'content']['application/json']['example'] = { piece: example_status[piece] }

  if not add_test_docs:
    return

  # Manually add relevant example parameters based on the current configuration
  # (for paths that require parameters)
  for route in app.routes:
    if isinstance(route, APIRoute):
      for method in route.methods:
        # check if path has id parameter
        has_id_param = False
        for param_name in route.param_convertors.keys():
          if 'id' in param_name and len(param_name) == 3:
            has_id_param = True
        if has_id_param:
          # generate examples for that id parameter
          live_examples = {}
          if len(route.tags) == 1:
            tag = route.tags[0]
            for i in  get_ctrl()._get_items(tag):
              live_examples[i.name] = {'value': i.id, 'summary': i.name}
          # find the matching parameter and add the examples to it
          for p in openapi_schema['paths'][route.path][method.lower()]['parameters']:
            if p['name'] == param_name:
              p['examples'] = live_examples

  return openapi_schema

YAML_DESCRIPTION = """| # The links in the description below are tested to work with redoc and may not be portable
    This is the AmpliPi home audio system's control server.

    # Configuration

    This web interface allows you to control and configure your AmpliPi device.
    At the moment the API is the only way to configure the AmpliPi.

    ## Try it out!

    __Using this web interface to test API commands:__

      1. Go to an API request
      1. Pick one of the examples
      2. Edit it
      3. Press try button, it will send an API command/request to the AmpliPi

    __Try using the get status:__

      1. Go to [Status -> Get Status](#get-/api/)
      2. Click the Try button, you will see a response below with the full status/config of the AmpliPi controller

    __Try creating a new group:__

      1. Go to [Group -> Create Group](#post-/api/group)
      2. Click Example
      3. Edit the zones and group name
      4. Click the try button, you will see a response with the newly created group

    __Here are some other things that you might want to change:__

      - [Stream -> Create new stream](#post-/api/stream)
      - [Zone -> Update Zone](#patch-/api/zones/-zid-) (to change the zone name)
      - [Preset -> Create preset](#post-/api/preset) (Have a look at the model to see what can be added here)

    # More Info

    Check out all of the different things you can do with this API:

      - [Status](#tag--status)
      - [Source](#tag--source)
      - [Zone](#tag--zone)
      - [Group](#tag--group)
      - [Stream](#tag--stream)
      - [Preset](#tag--preset)

    # OpenAPI

    This API is documented using the OpenAPI specification
"""

@lru_cache(2)
def create_yaml_doc(add_test_docs=True) -> str:
  """ Create the openapi yaml schema intended for display by rapidaoc

  We use yaml here for its human readability.
  """
  openapi = app.openapi()
  # use a placeholder for the description, multiline strings weren't using block formatting
  openapi['info']['description'] = '$REPLACE_ME$'
  yaml_s = yaml.safe_dump(openapi, sort_keys=False, allow_unicode=True)
  # fix the long description
  return yaml_s.replace('$REPLACE_ME$', YAML_DESCRIPTION)

# additional yaml version of openapi.json
# this is much more human readable
@app.get('/openapi.yaml', include_in_schema=False)
def read_openapi_yaml() -> Response:
  return Response(create_yaml_doc(), media_type='text/yaml')

@app.get('/openapi.json', include_in_schema=False)
def read_openapi_json():
  return app.openapi()

app.openapi = generate_openapi_spec

# Website

@app.get('/', include_in_schema=False)
@app.get('/{src}', include_in_schema=False)
def view(request: Request, src:int=0, ctrl:Api=Depends(get_ctrl, use_cache=False)):
  ctrl = get_ctrl()
  s = ctrl.get_state()
  context = {
    # needed for template to make response
    'request': request,
    # simplified amplipi state
    'cur_src': src,
    'sources': s.sources,
    'zones': s.zones,
    'groups': s.groups,
    'presets': s.presets,
    'inputs': ctrl.get_inputs(),
    'unused_groups': [unused_groups(src) for src in range(4)],
    'unused_zones': [unused_zones(src) for src in range(4)],
    'ungrouped_zones': [ungrouped_zones(src) for src in range(4)],
    'song_info': [song_info(src) for src in range(4)],
    'version': s.info.version,
  }
  return templates.TemplateResponse("index.html.j2", context, media_type='text/html')

def create_app(mock_ctrl=None, mock_streams=None, config_file=None, delay_saves=None, s:models.AppSettings=models.AppSettings()) -> FastAPI:
  """ Create the AmpliPi web app with a specific configuration """
  # specify old parameters
  if mock_ctrl: s.mock_ctrl = mock_ctrl
  if mock_streams: s.mock_streams = mock_streams
  if config_file: s.config_file = config_file
  if delay_saves: s.delay_saves = delay_saves
  # use a controller that has the specified configuration
  @lru_cache(1)
  def specific_ctrl():
    return Api(s)
  # replace the generic get_ctrl with the specific one we created above
  app.dependency_overrides[get_ctrl] = specific_ctrl
  return app

if __name__ == "__main__":
  """ Generate the openapi schema file in yaml """
  parser = argparse.ArgumentParser(description='Create the openapi yaml file describing the AmpliPi API')
  parser.add_argument('file', type=argparse.FileType('w'))
  args = parser.parse_args()
  with args.file as file:
    file.write(create_yaml_doc(add_test_docs=False))
