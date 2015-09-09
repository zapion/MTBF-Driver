# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import time

from gaiatest import GaiaTestCase
from gaiatest import DEFAULT_SETTINGS
from gaiatest import DEFAULT_PREFS
from gaiatest.apps.homescreen.app import Homescreen
from marionette_driver.by import By


class GaiaMtbfTestCase(GaiaTestCase):

    def launch_by_touch(self, name):
        self.apps.switch_to_displayed_app()
        icon = self.marionette.find_element(By.CSS_SELECTOR, '#icons [data-identifier*=' + name + ']')
        self.marionette.execute_script("arguments[0].scrollIntoView(false);", [icon])
        # Sleep because homescreen protect touch event when scrolling
        time.sleep(1)
        icon.tap()
        self.apps.switch_to_displayed_app()

    def horizontal_launch_by_touch(
            self,
            name,
            switch_to_frame=True,
            url=None,
            launch_timeout=None):
        '''
        This function is deprecated because homescreen was changed to vertical
        '''
        homescreen = Homescreen(self.marionette)
        self.marionette.switch_to_frame()
        hs = self.marionette.find_element('css selector', '#homescreen iframe')
        self.marionette.switch_to_frame(hs)
        homescreen.go_to_next_page()

        icon = self.marionette.find_element(
            'css selector',
            'li[aria-label="' + name + '"]:not([data-type="collection"])')

        while not icon.is_displayed() and homescreen.homescreen_has_more_pages:
            homescreen.go_to_next_page()

        get_current_page = "var pageHelper = window.wrappedJSObject.GridManager.pageHelper;return pageHelper.getCurrentPageNumber() > 0;"
        while not icon.is_displayed() and self.marionette.execute_script(get_current_page):
            self.marionette.execute_script('window.wrappedJSObject.GridManager.goToPreviousPage()')
            self.wait_for_condition(lambda m: m.find_element('tag name', 'body').get_attribute('data-transitioning') != 'true')
        icon.tap()

        self.marionette.switch_to_frame()

    def cleanup_storage(self):
        pass

    def cleanup_gaia(self, full_reset=True):

        # Turn on screen
        if not self.device.is_screen_enabled:
            self.device.turn_screen_on()

        # restore prefs from testvars
        default_prefs = DEFAULT_PREFS.copy()
        default_prefs.update(self.testvars.get('prefs', {}))
        default_prefs = self.modify_prefs(default_prefs)
        for name, value in default_prefs.items():
            if type(value) is int:
                self.data_layer.set_int_pref(name, value)
            elif type(value) is bool:
                self.data_layer.set_bool_pref(name, value)
            else:
                self.data_layer.set_char_pref(name, value)

        # unlock
        if self.data_layer.get_setting('lockscreen.enabled'):
            self.device.unlock()

        # kill FTU if possible
        if self.apps.displayed_app.name.upper() == "FTU":
            self.apps.kill(self.apps.displayed_app)

        if full_reset:
            # restore settings from testvars
            default_settings = DEFAULT_SETTINGS.copy()
            default_settings.update(self.testvars.get('settings', {}))
            default_settings = self.modify_settings(default_settings)
            for name, value in default_settings.items():
                self.data_layer.set_setting(name, value)

            # disable carrier data connection
            if self.device.has_mobile_connection:
                self.data_layer.disable_cell_data()

            if self.device.has_wifi:
                # Bug 908553 - B2G Emulator: support wifi emulation
                if not self.device.is_emulator:
                    self.data_layer.enable_wifi()
                    self.data_layer.forget_all_networks()
                    self.data_layer.disable_wifi()

            # don't remove contact data
            self.data_layer.remove_all_contacts()

            # reset to home screen
            self.device.touch_home_button()

        # disable sound completely
        self.data_layer.set_volume(0)

        # disable auto-correction of keyboard
        self.data_layer.set_setting('keyboard.autocorrect', False)

    def tearDown(self):
        time.sleep(1)
        self.device.touch_home_button()
        time.sleep(1)
        GaiaTestCase.tearDown(self)
