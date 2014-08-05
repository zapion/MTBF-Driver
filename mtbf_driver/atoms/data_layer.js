/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at http://mozilla.org/MPL/2.0/. */

'use strict';

var MTBFDataLayer = {

  setSetting: function(aName, aValue, aReturnOnSuccess) {
    SpecialPowers.addPermission('settings-readwrite', true, document);
    var returnOnSuccess = aReturnOnSuccess || aReturnOnSuccess === undefined;
    var setting = {};
    setting[aName] = aValue;
    console.log('setting ' + aName + ' to ' + aValue);
    var req = window.navigator.mozSettings.createLock().set(setting);
    req.onsuccess = function() {
      console.log('setting changed');
      if (returnOnSuccess) {
        marionetteScriptFinished(true);
      }
    };
    req.onerror = function() {
      console.log('error changing setting ' + req.error.name);
      marionetteScriptFinished(false);
    };
  },

  isWiFiConnected: function(aNetwork) {
    var manager = window.navigator.mozWifiManager;
    return manager.connection.status === 'connected' &&
           manager.connection.network.ssid === aNetwork.ssid;
  },

  connectToWiFi: function(aNetwork, aCallback) {
    var callback = aCallback || marionetteScriptFinished;
    var manager = window.navigator.mozWifiManager;

    if (this.isWiFiConnected(aNetwork)) {
      console.log("already connected to network with ssid '" +
                  aNetwork.ssid + "'");
      callback(true);
    }
    else {
      var req;
      if (window.MozWifiNetwork === undefined) {
        req = manager.associate(aNetwork);
      } else {
        req = manager.associate(new window.MozWifiNetwork(aNetwork));
      }

      req.onsuccess = function() {
        console.log("waiting for connection status 'connected'");
        waitFor(
          function() {
            console.log("success connecting to network with ssid '" +
                        aNetwork.ssid + "'");
            callback(true);
          },
          function() {
            console.log('connection status: ' + manager.connection.status);
            return manager.connection.status === 'connected';
          }
        );
      };

      req.onerror = function() {
        console.log('error connecting to network ' + req.error.name);
        callback(false);
      };
    }
  },

  enableWiFi: function() {
    var manager = window.navigator.mozWifiManager;
    if (!manager.enabled) {
      waitFor(
        function() { marionetteScriptFinished(true); },
        function() {
          console.log('wifi enabled status: ' + manager.enabled);
          return manager.enabled === true;
      });
      this.setSetting('wifi.enabled', true, false);
    }
    else {
      console.log('wifi already enabled');
      marionetteScriptFinished(true);
    }
  }
};
