import peewee as pw
from db_connect import BaseModel
import managers.user_manager as user_manager
import managers.group_manager as group_manager
import managers.doc_manager as doc_manager
import datetime
from managers.user_manager import Queue as Queue
from managers.user_manager import Request as Request
import managers.notifier


class History(BaseModel):
    """Data model for all checkout and return operations
    """
    OperationID = pw.PrimaryKeyField()
    renewed = pw.BooleanField(default=False)
    user = pw.ForeignKeyField(user_manager.User, related_name='operations')
    copy = pw.ForeignKeyField(doc_manager.Copy, related_name='operations')
    librarian_co = pw.CharField()
    date_check_out = pw.DateField(formats='%Y-%m-%d')
    librarian_re = pw.CharField(null=True)
    date_return = pw.DateField(formats='%Y-%m-%d', null=True)


class Booking_system:
    """Booking system class
    """
    __fine = 100

    def check_out(self, doc, user, librarian):
        """Check outs copy by document and user entries. If there is no available copy, user is placed in queue
        """
        if (user.group == group_manager.Group.get(group_manager.Group.name == 'Deleted')):
            return (4, None)  # User is deleted
        if doc.active == False:
            return (3, None)  # Inactive document
        if 'reference' in doc.keywords:
            return (2, None)  # Document is reference

        for entry in self.get_user_history(user):
            if (entry.date_return == None and entry.copy.get_doc() == doc):
                return (6, None)  # User has a copy of this document

        # Check if copy reserved. If it is not reserved, method check_out_reserved returns error
        # and checks out document otherwise
        reserved = self.__check_out_reserved(doc, user, librarian)
        if (reserved[0] == 0):
            return reserved

        # find copy that is not checked out
        copy_query = doc_manager.Copy.select().where(doc_manager.Copy.active == True,
                                                     doc_manager.Copy.checked_out == 0)
        if (len(copy_query) != 0):
            copy = copy_query.get()
            copy.checked_out = 2
            copy.save()
            current_date = datetime.date.today()
            res = History.create(user=user, copy=copy, librarian_co=librarian, date_check_out=current_date)
            return (0, res)  # successfully checked out

        # Push to the queue if there is no free copy
        res = Queue.push_to_queue(doc, user)
        if (res == None):
            return (7, None)  # Already is in the queue
        return (1, None)  # Placed in the queue

    def __check_out_reserved(self, doc, user, librarian):
        """Checks out reserved copy of document (Supposed to be called only from check_out method)
        """
        # Get Queue entry and check out assigned copy
        entry = Queue.get_to_remove(doc, user)
        if (entry == None):
            return (1, None)
        copy = entry.assigned_copy
        copy.checked_out = 2
        copy.save()
        current_date = datetime.date.today()
        res = History.create(user=user, copy=copy, librarian_co=librarian, date_check_out=current_date)
        entry.delete_instance()  # Delete entry after check out
        return (0, res)  # successfully checked out

    def return_by_entry(self, entry, librarian):
        """Return copy by "History" entry
        """
        if (entry.date_return != None):
            return 1  # Copy is already returned
        current_date = datetime.date.today()
        entry.date_return = str(current_date)
        entry.librarian_re = librarian
        entry.save()
        entry.copy.checked_out = 0
        entry.copy.save()
        entry.user.fine += self.check_overdue(entry)
        entry.user.save()
        copy = doc_manager.Copy.get_by_id(entry.copy.CopyID)
        return self.proceed_free_copy(copy, librarian)

    def proceed_free_copy(self, copy, librarian):
        """Proceed free copy. Assign to people in the queue or check out if it is requested
        """
        if (copy.get_doc().requested == True):
            doc = copy.get_doc()
            user = Request.get_user(doc)
            self.check_out(doc, user, librarian)
            Request.close_request(user, doc, librarian)
            return 5  # Checked out to user in outstanding request
        queue_next = Queue.get_user_from_queue(copy)
        if queue_next == None:
            return 0  # Successfully returned
        # Inform user about free copy here <-
        text = "Dear %s,\nQueued document \"%s\" for you is ready.\n" \
               % (queue_next.name + " " + queue_next.surname, copy.get_doc().title)
        managers.notifier.send_message(queue_next.email, "Document is ready", text)
        return 4  # Assigned to someone in the queue

    def return_by_copy(self, copy, librarian):
        """Return copy
        """
        query = History.select().where((History.date_return.is_null(True)) & (History.copy == copy))
        if (len(query) == 0):
            return 3  # No entry found
        if (len(query) > 1):
            print('Houston, we have a problems. Return_by_copy, booking system')
            return 2  # Internal error
        entry = query.get()
        return self.return_by_entry(entry, librarian)

    def renew_by_entry(self, entry, librarian):
        """Renew copy for certain user using History entry"""
        if (entry.date_return != None):
            return (1, None)  # Copy is already returned
        if (self.check_overdue(entry) != 0):
            return (2, None)  # Copy is overdued
        if (entry.copy.get_doc().requested == True):
            return (3, None)  # Document is under outstanding request
        if (entry.renewed == True):
            return (6, None)  # Copy has been already renewed
        current_date = datetime.date.today()
        entry.date_return = str(current_date)
        entry.librarian_re = librarian
        entry.save()
        res = History.create(user=entry.user, copy=entry.copy, librarian_co=librarian,
                             date_check_out=current_date, renewed=True)
        return (0, res)

    def renew_by_copy(self, copy, librarian):
        """Renew by copy"""
        query = History.select().where((History.date_return.is_null(True)) & (History.copy == copy))
        if (len(query) == 0):
            return (4, None)  # Copy is not checked out
        if (len(query) > 1):
            return (5, None)  # Internal error
        entry = query.get()
        return self.renew_by_entry(entry, librarian)

    def outstanding_request(self, doc, user, librarian):
        """Places outstanding request for certain document for list of users.
        Returns (code, history entry (if there was free copy after queue abandon) or request entry)"""
        if (user.group == group_manager.Group.get(group_manager.Group.name == 'Deleted')):
            return 4  # User is deleted
        if doc.active == False:
            return 3  # Document is inactive
        if 'reference' in doc.keywords:
            return 2  # Document is reference

        for entry in user.operations:
            if (entry.date_return == None and entry.copy.get_doc() == doc):
                return 6  # User already has copy of this document

        # If any request for this document exists, cancel it
        entry = Request.get_user(doc)
        if (entry != None):
            entry.active = False
            entry.save()
            if (Request.get_user(doc) != None):
                print('Houston, we have a problems. Outstanding request, booking system')
        # Check if there is available copy
        copies = doc.get_document_copies()
        for copy in copies:
            if (copy.active == True and copy.checked_out == 0):
                return (2, None)  # There is free copy
        Queue.red_button(doc)
        copies = doc.get_document_copies()  # update copies
        # if we have free copies after deleting queue, check out to users who are in request
        for copy in copies:
            if (copy.active == True and copy.checked_out == 0):
                res = self.check_out(doc, user, librarian)
                return (1, res)  # One of copies became free after flushing the queue
        # Placing request
        res = Request.place_request(doc, user, librarian)
        return (0, res)  # Request is placed
