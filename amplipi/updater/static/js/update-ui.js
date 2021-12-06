  /*
   * Some helper functions to work with our UI and keep our code cleaner
   */

// Adds an entry to our debug area
function ui_add_log(message, color)
{
  var d = new Date();

  var dateString = (('0' + d.getHours())).slice(-2) + ':' +
    (('0' + d.getMinutes())).slice(-2) + ':' +
    (('0' + d.getSeconds())).slice(-2);

  color = (typeof color === 'undefined' ? 'muted' : color);

  var template = $('#debug-template').text();
  template = template.replace('%%date%%', dateString);
  template = template.replace('%%message%%', message);
  template = template.replace('%%color%%', color);

  $('#debug').find('li.empty').fadeOut(); // remove the 'no messages yet'
  $('#debug').prepend(template);
}

// Updates a file progress, depending on the parameters it may animate it or change the color.
function ui_multi_update_file_progress(id, percent, color, active)
{
  color = (typeof color === 'undefined' ? false : color);
  active = (typeof active === 'undefined' ? true : active);

  var bar = $('#uploaderFile' + id).find('div.progress-bar');

  bar.width(percent + '%').attr('aria-valuenow', percent);
  bar.toggleClass('progress-bar-striped progress-bar-animated', active);

  if (percent === 0){
    bar.html('');
  } else {
    bar.html(percent + '%');
  }

  if (color !== false){
    bar.removeClass('bg-success bg-info bg-warning bg-danger');
    bar.addClass('bg-' + color);
  }
}

function ui_begin_update() {
  // TODO: hide file uploader
  // setup SSE events, for intermediate step info
  // TODO: start spinner
  var source = new EventSource("update/install/progress");
  source.onmessage = function(event) {
    var data = JSON.parse(event.data);
    ui_show_update_progress(data);
    if (data.type == 'success' || data.type == 'failed') {
      source.close();
      if (data.type == 'success') {
        ui_reboot_app();
      }
    }
  };
  fetch("update/install"); // start the install TODO: check response
}

function ui_reboot_app() {
  // initiate a reboot
  fetch("update/restart").then(function (response) {
    if (response.ok) {
      ui_add_log('Restarting AmpliPi Update server to finish update', 'info')
      setTimeout(ui_check_after_reboot, 3000)
        // TODO: on fail -> show info on how to recover
    } else {
      ui_add_log('Error restarting update server: ' + response, 'danger')
    }
  }).catch( err => {ui_add_log('Error restarting update server: ' + err.message, 'danger');})
}

function ui_check_after_reboot() {
  // check reported version
  r = fetch("update/version").then(function (response){
    response.json().then(function(json) {
      ui_add_log(json.version, 'info')
      ui_add_log('Done restarting updater', 'info')
      ui_add_log('Redirecting back to AmpliPi server', 'info')
      // TODO: stop spinner and show good/done
      setTimeout(ui_redirect_to_amplipi, 3000)
    }).catch( err => {ui_add_log('Error checking version: ' + err.message, 'danger');});
  }).catch( err => {ui_add_log('Error checking version: ' + err.message, 'danger');})
}

function ui_redirect_to_amplipi() {
  window.location = window.location.toString().replace(":5001/update", ":80")
}

function ui_show_update_progress(status) {
  // assumes status {'message': str, 'type': 'info'|'warning'|'error'|'success'|'failed'}
  let color = (status.type == 'error' || status.type == 'failed') ? 'danger' : status.type;
  if (status.message.trim().length > 0) {
    ui_add_log(status.message, color);
  }
}

function ui_upload_software_update() {
  ui_disable_buttons();
  let data = new FormData();
  let file = $('#update-file-selector')[0].files[0];
  data.append('file', file);
  try {
    fetch('/update/upload', {
      method: 'POST',
      body: data,
    }).then((response) => {
      ui_add_log('file uploaded', 'info');
      ui_begin_update();
    });
  } catch(e) {
    ui_add_log(e, 'danger');
  }
}

function ui_disable_buttons() {
  $('#submit-latest-update, #submit-older-update, #submit-custom-update').addClass('disabled');
  $('#submit-latest-update, #submit-older-update, #submit-custom-update').empty().append('Updating <i class="fas fa-circle-notch"></i>');
  $('#older-update-sel, #update-file-selector').attr('disabled', '');
}

let md = new remarkable.Remarkable();

function ui_select_release(sel) {
  selected = $(sel).find(':selected');
  $('#submit-older-update').toggleClass('disabled', selected.data('version'));
  $('#older-update-desc').empty().append(md.render(selected.data('desc')));
}

function ui_start_software_update(url, version) {
  ui_disable_buttons();
  req = {"url" : url, "version" : version};
  try {
    fetch('/update/download', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json;charset=utf-8'
      },
      body: JSON.stringify(req)
    }).then((response) => {
      ui_add_log(`dowloaded "${version}" release`, 'info');
      ui_begin_update();
    });
  } catch(e) {
    ui_add_log(e, 'danger');
  }
}

function ui_show_offline_message() {
  $('#latest-update-name').empty().append('Unable to automatically check for latest release <i class="fas fa-times text-danger"></i>');
  OFFLINE_INFO = 'To update:\n\n\
  1. Download the latest tar.gz release file from our \n\
      [GitHub releases page](https://github.com/micro-nova/AmpliPi/releases).\n\
  1. Use the the **Custom** update tab to upload the release.'
  $('#latest-update-desc').append(md.render(OFFLINE_INFO));
}

// get the current AmpliPi version
let version = 'unknown';
fetch('/update/version').then((resp) => {
  resp.json().then((info) => {
    version = info.version;
  });
});

// fetch the GH Releases and populate the release selector and latest release
fetch('https://api.github.com/repos/micro-nova/AmpliPi/releases').then((resp) => {
  console.log(resp);
  if (resp.status != 200) {
    ui_show_offline_message();
    return
  }
  resp.json().then((releases) => {
    if (releases.length == 0) {
      ui_show_offline_message();
      return
    }
    // show the latest release
    latest_release = releases[0];
    if (latest_release.tag_name == version) {
      console.log('already up to date');
      $('#latest-update-name').empty().append('Your system is up to date  <i class="fas fa-check-circle text-success"></i>')
    } else {
      // show the release info with its markdown from GH
      $('#submit-latest-update').removeClass('d-none');
      $('#latest-update-name').text(latest_release.name);
      $('#latest-update-desc').append(md.render(latest_release.body));
      // embedd the url and version so it can be passed on click
      $('#latest-update').attr('data-url', latest_release.tarball_url);
      $('#latest-update').attr('data-version', latest_release.tag_name);
    }
    // populate release selector
    for (const release of releases) {
      console.log(`found "${release.name}" - ${release.tarball_url}`);
      $('#older-update-sel').append(`<option value="${release.tarball_url}"
                                             data-version="${release.tag_name}"
                                             data-name="${release.name}"
                                             data-desc="${release.body}">
                                             ${release.name}
                                     </option>`);
    }
  }).catch((err) => { ui_show_offline_message(); });
}).catch((err) => { ui_show_offline_message(); });
