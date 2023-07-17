import csv
import urllib.request
import re
import os
import requests
import json
import subprocess

class Image:
    def __init__(self, name, tag, sha):
        self.name = name
        self.tag = tag
        self.sha = sha
        fullname = name
        if tag != None and tag != '':
            fullname = fullname + ":" + tag
        fullname = fullname + sha
        self.full_name = fullname
    
    def print(self):
        print('<' + self.full_name + '>')
        print('name=%s tag=%s sha=%s' % (self.name, self.tag, self.sha))
    
    @staticmethod
    def parse(raw_name):
        full_name = raw_name
        sha_offset = raw_name.find('@sha256:')
        if sha_offset != -1:
            sha = raw_name[sha_offset:]
            raw_name = raw_name[:sha_offset]
        tag_offset = raw_name.find(':')
        if tag_offset != -1:
            tag = raw_name[tag_offset + 1:]
            name = raw_name[:tag_offset]
        else:
            name = raw_name
            tag = None
        return Image(name, tag, sha)

class ImageMapping:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.existing_repositories = set()
        self.mapping = {}
        self.load_mapping()

    def load_mapping(self):
        with open(self.cache_file) as csvfile:
            reader = csv.reader(csvfile, delimiter=',')
            for row in reader:
                (gcrimage, newimage, tag, sha) = row
                self.existing_repositories.add(newimage)
                self.mapping[gcrimage] = Image(newimage, tag, sha)
    
    def is_repository_exists(self, repository):
        return repository in self.existing_repositories
    
    def mark_as_existing(self, repository):
        self.existing_repositories.add(repository)
    def add_mapping(self, gcrimage, image):
        if gcrimage in self.mapping:
            if self.mapping[gcrimage].full_name != image.full_name:
                print('Existing mapping:' + self.mapping[gcrimage].full_name)
                print('New image:' + image.full_name)
                raise Exception('Conflict:' + gcrimage)
        else:
            self.mapping[gcrimage] = image
            self.existing_repositories.add(image.name)

    def save(self):
        print('Saving ' + self.cache_file)
        with open(self.cache_file, 'w') as csvfile:
            writer = csv.writer(csvfile, delimiter=',')
            for gcrimage,image in self.mapping.items():
                writer.writerow([gcrimage, image.name, image.tag, image.sha])

class ImageTransformer:
    def __init__(self, image_mapping):
        self.image_mapping = image_mapping

    def process(self, gcrimage):
        print('processing ' + gcrimage + ' ...')
        if gcrimage in self.image_mapping.mapping:
            print('Already exist.')
            return self.image_mapping.mapping[gcrimage].full_name
        os.system('docker pull ' + gcrimage)
        image = Image.parse(gcrimage)

        # hack
        new_repository = "knativecn/" +image.name.replace('/', '.')
        # if new_repository.startswith('knativecn/gcr.io.knative-releases'):
        #    new_repository = new_repository.replace('gcr.io.knative-releases', 'gcr.io')
        
        if not self.image_mapping.is_repository_exists(new_repository):
            # self.create_dockerhub_repo(gcrimage, new_repository)
            self.image_mapping.mark_as_existing(new_repository)
        new_image = new_repository
        if image.tag != None:
            new_image = new_repository + ":" + image.tag
        os.system('docker tag %s %s' %(gcrimage, new_image))
        output = subprocess.check_output('docker push %s' % new_image, shell=True)
        print('====>' + str(output))
        pattern = r'digest: sha256:[a-f0-9]{64}'
        digest = re.findall(pattern, str(output))[0].replace('digest: ', '@')
        print('====>' + digest)
        dockerhub_image = Image(new_repository, image.tag, digest)
        self.image_mapping.add_mapping(gcrimage, dockerhub_image)
        self.image_mapping.save()

        return dockerhub_image.full_name

    def create_dockerhub_repo(self, gcrimage, new_repository):
        print('Creating ' + new_repository)
        headers = {'Authorization' : 'JWT %s' % os.getenv('TOKEN'), "Content-Type": "application/json"}
        arr = new_repository.split('/')
        data = json.dumps({'namespace': arr[0], 'name': arr[1], 'is_private': False, 'description': 'Dockerhub mirror for ' + gcrimage})
        response = requests.post('https://hub.docker.com/v2/repositories/', data = data, headers = headers)
        if response.status_code != 201:
            if response.status_code != 400 or response.text.find('already exists') == -1:
                print('%d: %s' % (response.status_code, response.text))
                raise Exception('Failed to create ' + new_repository)
        print('Created.')

class ReleseFileTransformer:
    def __init__(self, image_mapping):
        self.image_transformer = ImageTransformer(image_mapping)

    def transform(self, remote_url):
        (component, tag, release_type) = self.parse_release(remote_url)
        converted_lines = list(self.read_and_process_from_url(remote_url))
        output_file = "output/%s-%s-%s" % (component, tag, release_type)
        self.save_result(converted_lines, output_file)
    
    def parse_release(self, url):
        if url.startswith('https://github.com/tektoncd') or url.startswith('https://github.com/knative'):
            name = re.sub(r'https://github.com/', '', url)
            arr = name.split('/')
            group = arr[0]
            component = arr[1]
            tag = arr[4]
            if tag.find('-') != -1:
                tag = tag.split('-')[1]
            release_type = arr[5]
            return (group + '-' + component, tag, release_type)
        else:
            raise Exception('Unknown release:' + url)
    def read_and_process_from_url(self, url):
        for line in urllib.request.urlopen(url):
            text = line.decode('utf-8')
            yield self.process_release_file_line(text)

    def find_image_names(self, line):
        gcrimages = self.find_image_names_by_prefix(r'gcr\.io', line) 
        cgrimages = self.find_image_names_by_prefix(r'cgr\.dev', line)
        return gcrimages + cgrimages

    def find_image_names_by_prefix(self, prefix, line):
        pattern = prefix + r'(?:/[a-z-_\.]+)+(?::v?\d+(?:\.\d+)*)?(?:@sha256:[a-f0-9]{64})?'
        return re.findall(pattern, line)
    def process_release_file_line(self, text):
        if text.lstrip().rstrip().startswith('#'):
            return text
        replaced_text = text
        for image in self.find_image_names(text):
            new_image = self.image_transformer.process(image)
            replaced_text = replaced_text.replace(image, new_image)
        return replaced_text
    
    def save_result(self, lines, output_file):
        with open(output_file, 'w') as file:
            file.writelines(lines)

def next_release_file():
    with open('releases.txt') as file:
        for line in file.readlines():
            line = line.lstrip().rstrip()
            if not line.startswith('#') and not line == '':
                yield line

def main():
    print('Synchronizing from gcr.io to dockerhub...')
    image_mapping = ImageMapping('mapping.csv')
    release_transformer = ReleseFileTransformer(image_mapping)
    for url in next_release_file():
        print('Translating ' + url)
        release_transformer.transform(url)

def test():
    process_remote_image('gcr.io/knative-releases/knative.dev/serving/cmd/domain-mapping-webhook:v0.4.4@sha256:26cb5fdb9a5fe575919869331172e2b73de01084c043191748fbd45ba443abc2')

if __name__ == '__main__':
    main()
